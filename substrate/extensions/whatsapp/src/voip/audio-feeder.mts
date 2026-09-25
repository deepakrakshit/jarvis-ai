/**
 * JARVIS WhatsApp VoIP Engine - Audio Feeder.
 *
 * Meters realtime PCM frames or decodes audio source at chunk-cadence
 * for the WebAssembly VoIP uplink.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

const LOW_WATERMARK_CHUNKS = 16;
const MAX_QUEUED_CHUNKS = 1024;
const DEFAULT_WARMUP_MS = 500;

export class AudioFeeder {
  #proc: ChildProcessWithoutNullStreams | null = null;
  #pending = Buffer.alloc(0);
  #queue: Float32Array[] = [];
  #emitTimer: NodeJS.Timeout | null = null;
  #nextEmitAtMs = 0;
  #warmupUntilMs = 0;

  #active = false;
  #realtimeResidual: Float32Array | null = null;

  droppedChunks = 0;
  underflowChunks = 0;
  bytesProduced = 0;
  chunksEmitted = 0;

  constructor(
    private readonly sampleRate: number,
    private readonly channels: number,
    private readonly framesPerChunk: number,
    private readonly onChunk: (chunk: Float32Array) => void,
    private readonly source: string = "silence",
  ) {}

  pushChunk = (data: Float32Array): void => {
    if (!data || data.length === 0) return;
    const chunkSamples = this.framesPerChunk * this.channels;

    let combined: Float32Array;
    if (this.#realtimeResidual && this.#realtimeResidual.length > 0) {
      combined = new Float32Array(this.#realtimeResidual.length + data.length);
      combined.set(this.#realtimeResidual, 0);
      combined.set(data, this.#realtimeResidual.length);
      this.#realtimeResidual = null;
    } else {
      combined = data;
    }

    let offset = 0;
    while (offset + chunkSamples <= combined.length) {
      if (this.#queue.length >= MAX_QUEUED_CHUNKS) {
        this.droppedChunks += 1;
        break;
      }
      const slice = combined.subarray(offset, offset + chunkSamples);
      const out = new Float32Array(chunkSamples);
      out.set(slice);
      this.bytesProduced += chunkSamples * Float32Array.BYTES_PER_ELEMENT;
      this.#queue.push(out);
      offset += chunkSamples;
    }

    if (offset < combined.length) {
      this.#realtimeResidual = new Float32Array(combined.subarray(offset));
    }
  };

  clearQueue = (): void => {
    this.#queue = [];
    this.#realtimeResidual = null;
  };

  getQueueLength = (): number => {
    return this.#queue.length;
  };

  start = (): void => {
    if (this.#active) return;
    this.#active = true;

    const chunkSamples = this.framesPerChunk * this.channels;
    const chunkIntervalMs = (this.framesPerChunk / this.sampleRate) * 1000;

    if (this.source !== "realtime") {
      const inputArgs = this.#resolveInputArgs();

      this.#proc = spawn("ffmpeg", [
        "-hide_banner",
        "-loglevel", "error",
        "-thread_queue_size", "512",
        ...inputArgs,
        "-f", "f32le",
        "-ac", String(this.channels),
        "-ar", String(this.sampleRate),
        "pipe:1",
      ]);

      const chunkBytes = chunkSamples * Float32Array.BYTES_PER_ELEMENT;

      this.#proc.stdout.on("data", (chunk: Buffer) => {
        this.#pending = Buffer.concat([this.#pending, chunk]);
        while (this.#pending.length >= chunkBytes) {
          if (this.#queue.length >= MAX_QUEUED_CHUNKS) {
            this.#proc?.stdout.pause();
            break;
          }
          const frame = this.#pending.subarray(0, chunkBytes);
          this.#pending = this.#pending.subarray(chunkBytes);
          const out = new Float32Array(chunkSamples);
          out.set(new Float32Array(frame.buffer, frame.byteOffset, chunkSamples));
          this.bytesProduced += chunkBytes;
          this.#queue.push(out);
        }
      });

      this.#proc.stderr.on("data", (chunk: Buffer) => {
        process.stderr.write(`[AudioFeeder] ${chunk.toString().trim()}\n`);
      });

      this.#proc.on("exit", (code) => {
        if (code !== 0 && code !== null) {
          process.stderr.write(`[AudioFeeder] ffmpeg exited with code=${code}\n`);
        }
        this.#proc = null;
      });
    }

    this.#nextEmitAtMs = 0;
    this.#warmupUntilMs = this.source === "realtime" ? 0 : Date.now() + DEFAULT_WARMUP_MS;
    this.#scheduleNext(chunkSamples, chunkIntervalMs);
  };

  stop = (): void => {
    this.#active = false;
    if (this.#emitTimer) {
      clearTimeout(this.#emitTimer);
      this.#emitTimer = null;
    }
    this.#proc?.kill("SIGTERM");
    this.#proc = null;
    this.#pending = Buffer.alloc(0);
    this.#queue = [];
    this.#realtimeResidual = null;
    this.#warmupUntilMs = 0;
  };

  #resolveInputArgs = (): string[] => {
    if (!this.source || this.source === "silence") {
      return ["-f", "lavfi", "-i", `aevalsrc=0:d=3600:s=${this.sampleRate}`];
    }
    if (this.source.startsWith("lavfi:")) {
      return ["-f", "lavfi", "-i", this.source.slice("lavfi:".length)];
    }
    return ["-i", this.source];
  };

  #scheduleNext = (chunkSamples: number, chunkIntervalMs: number): void => {
    if (!this.#active) return;
    const now = Date.now();
    if (this.#nextEmitAtMs === 0) this.#nextEmitAtMs = now;
    const delayMs = Math.max(0, this.#nextEmitAtMs - now);

    this.#emitTimer = setTimeout(() => {
      this.#emitTimer = null;
      if (!this.#active) return;
      if (this.source !== "realtime" && this.#queue.length < LOW_WATERMARK_CHUNKS && Date.now() < this.#warmupUntilMs) {
        this.#nextEmitAtMs = Date.now() + 10;
        this.#scheduleNext(chunkSamples, chunkIntervalMs);
        return;
      }
      this.#flushOne(chunkSamples);
      this.#nextEmitAtMs += chunkIntervalMs;
      this.#scheduleNext(chunkSamples, chunkIntervalMs);
    }, delayMs);
  };

  #flushOne = (chunkSamples: number): void => {
    let nextChunk = this.#queue.shift();
    if (!nextChunk) {
      nextChunk = new Float32Array(chunkSamples);
      this.underflowChunks += 1;
    }
    this.chunksEmitted += 1;
    this.onChunk(nextChunk);
    if (this.#proc?.stdout.isPaused() && this.#queue.length <= MAX_QUEUED_CHUNKS / 4) {
      this.#proc.stdout.resume();
    }
  };
}
