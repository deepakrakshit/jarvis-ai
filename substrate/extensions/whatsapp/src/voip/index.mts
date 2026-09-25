/**
 * JARVIS WhatsApp VoIP Engine.
 *
 * Full-duplex VoIP WebAssembly runtime and signaling integration for JARVIS.
 */
import { EventEmitter } from "node:events";
import { randomBytes, createHmac } from "node:crypto";
import { resolve } from "node:path";

import { WasmEngine } from "./wasm-engine.mjs";
import { RelayRtcTransport, type RelayListUpdatePayload } from "./relay-transport.mjs";
import { SignalingBridge } from "./signaling.mjs";
import { AudioFeeder } from "./audio-feeder.mjs";
import { CallState, type VoipSdkConfig } from "./types.mjs";

export type { VoipSdkConfig, CallOptions, CallEvents, AudioConfig } from "./types.mjs";
export { CallState } from "./types.mjs";

const SHA256_LEN = 32;

const loadBaileys = async (): Promise<any> => {
  try {
    return await import("@whiskeysockets/baileys");
  } catch {
    throw new Error(
      "Could not import @whiskeysockets/baileys. Install it as a peer dependency.",
    );
  }
};

const toBareJid = (jid: string): string => {
  if (!jid) return jid;
  const at = jid.indexOf("@");
  if (at < 0) return jid;
  const user = jid.slice(0, at).split(":")[0];
  return `${user}@${jid.slice(at + 1)}`;
};

const computeHkdf = (
  key: Uint8Array,
  salt: Uint8Array | null,
  info: Uint8Array,
  length: number,
): Uint8Array => {
  const effectiveSalt = salt && salt.length > 0 ? Buffer.from(salt) : Buffer.alloc(SHA256_LEN, 0);
  const prk = createHmac("sha256", effectiveSalt).update(key).digest();
  const blocks = Math.ceil(length / SHA256_LEN);
  const okm = Buffer.alloc(blocks * SHA256_LEN);
  let prev = Buffer.alloc(0);
  for (let i = 1; i <= blocks; i += 1) {
    prev = createHmac("sha256", prk)
      .update(prev)
      .update(info)
      .update(Buffer.from([i]))
      .digest();
    prev.copy(okm, (i - 1) * SHA256_LEN);
  }
  return new Uint8Array(okm.buffer, okm.byteOffset, length);
};

const computeHmacSha256 = (data: Uint8Array, key: Uint8Array): Uint8Array => {
  const result = createHmac("sha256", Buffer.from(key)).update(data).digest();
  return new Uint8Array(result.buffer, result.byteOffset, result.byteLength);
};

const isCallReceiptNode = (node: any): boolean => {
  if (node?.tag !== "receipt") return false;
  const child = Array.isArray(node.content) ? node.content[0] : null;
  return !!(child?.attrs?.["call-id"] || child?.attrs?.call_id);
};

/** A live or recently-ended call. */
export class ActiveCall extends EventEmitter {
  #state: CallState = CallState.Idle;
  #endResolver!: (reason: string) => void;
  readonly #endPromise: Promise<string>;
  #endTimer: NodeJS.Timeout | null = null;
  #ended = false;

  /** @internal mirrors the source path for the audio feeder */
  _audioSource: string = "silence";
  /** @internal attached by VoipClient when audio capture starts */
  _feeder: AudioFeeder | null = null;
  #pendingAudioBeforeFeeder: Float32Array[] = [];

  constructor(
    public readonly callId: string,
    private readonly engine: WasmEngine,
    durationMs: number,
  ) {
    super();
    this.#endPromise = new Promise((res) => { this.#endResolver = res; });
    if (durationMs > 0) {
      this.#endTimer = setTimeout(() => this.end(), durationMs);
    }
  }

  get state(): CallState { return this.#state; }

  end = (): void => {
    if (this.#ended) return;
    if (this.#endTimer) { clearTimeout(this.#endTimer); this.#endTimer = null; }
    try { this.engine.endCall(0, true); } catch {}
    // Fallback in case WASM doesn't emit state 0 immediately
    setTimeout(() => {
      this._forceEnd("local_ended");
    }, 400).unref();
  };

  mute = (muted: boolean): void => {
    try { this.engine.setMute(muted); } catch {}
  };

  /** Push realtime PCM frames (16 kHz mono Float32Array) into the outbound RTP stream. */
  pushAudio = (pcm: Float32Array): void => {
    if (this.#ended) return;
    if (this._feeder) {
      this._feeder.pushChunk(pcm);
    } else {
      this.#pendingAudioBeforeFeeder.push(pcm);
    }
  };

  /** Clear any queued outbound audio immediately (for barge-in / interruption). */
  clearAudioQueue = (): void => {
    this.#pendingAudioBeforeFeeder = [];
    this._feeder?.clearQueue();
  };

  /** Get current number of queued audio chunks. */
  getAudioQueueLength = (): number => {
    return (this._feeder?.getQueueLength() ?? 0) + this.#pendingAudioBeforeFeeder.length;
  };

  /** @internal */
  _attachFeeder = (feeder: AudioFeeder): void => {
    this._feeder = feeder;
    if (this.#pendingAudioBeforeFeeder.length > 0) {
      for (const chunk of this.#pendingAudioBeforeFeeder) {
        feeder.pushChunk(chunk);
      }
      this.#pendingAudioBeforeFeeder = [];
    }
  };

  waitForEnd = (): Promise<string> => this.#endPromise;

  /** @internal — called by VoipClient on WASM call-state change */
  _updateState = (state: number): void => {
    this.#state = state as CallState;
    if (state === CallState.PreacceptReceived) this.emit("ringing");
    else if (state === CallState.Active) this.emit("connected");
    else if (state === CallState.Idle || state === CallState.Ending) {
      this._forceEnd("ended");
    }
  };

  /** @internal */
  _emitAudio = (pcm: Float32Array): void => {
    if (this.#ended) return;
    this.emit("audio", pcm);
  };

  /** @internal */
  _forceEnd = (reason: string): void => {
    if (this.#ended) return;
    this.#ended = true;
    this.#state = CallState.Idle;
    if (this.#endTimer) { clearTimeout(this.#endTimer); this.#endTimer = null; }
    try {
      this._feeder?.stop();
    } catch {}
    this._feeder = null;
    this.#pendingAudioBeforeFeeder = [];
    this.emit("ended", reason);
    this.#endResolver(reason);
  };
}

/** Top-level client. Connects to WhatsApp and lets you place calls. */
export class VoipClient {
  readonly #config: VoipSdkConfig;
  #engine: WasmEngine | null = null;
  #relay: RelayRtcTransport | null = null;
  #signaling: SignalingBridge | null = null;
  #sock: any = null;
  #activeCall: ActiveCall | null = null;
  #baileys: any = null;

  // Capture state populated when WASM negotiates audio params
  #capturePtr = 0;
  #captureChunkBytes = 0;
  #captureSampleRate = 16000;
  #captureChannels = 1;
  #captureFramesPerChunk = 320;
  #feeder: AudioFeeder | null = null;

  constructor(config: VoipSdkConfig) {
    this.#config = config;
  }

  /** Connect to WhatsApp and bring up the WASM VoIP stack. */
  connect = async (): Promise<void> => {
    this.#baileys = await loadBaileys();
    const { useMultiFileAuthState, default: makeWASocket, DisconnectReason } = this.#baileys;
    const makeSocket: (opts: any) => any =
      makeWASocket ?? this.#baileys.makeWASocket ?? this.#baileys;

    const authDir = resolve(this.#config.authDir);
    const { state, saveCreds } = await useMultiFileAuthState(authDir);

    let version: [number, number, number] | undefined;
    try {
      const v = await this.#baileys.fetchLatestBaileysVersion?.();
      if (v?.version) version = v.version;
    } catch {}

    let logger: any;
    try {
      const pinoModule = await import("pino");
      const pino = (pinoModule as any).default ?? pinoModule;
      logger = pino({ level: "silent" });
    } catch {
      logger = {
        level: "silent",
        child: () => logger,
        trace: () => {},
        debug: () => {},
        info: () => {},
        warn: () => {},
        error: () => {},
        fatal: () => {},
      };
    }

    const createSocket = () => makeSocket({
      version,
      auth: state,
      emitOwnEvents: true,
      logger,
      syncFullHistory: true,
    });

    // Connect with auto-reconnect on transient closes or post-QR restarts.
    await new Promise<void>((resolveOpen, rejectOpen) => {
      let opened = false;
      let retries = 0;
      const maxRetries = 25;

      const connectSocket = () => {
        this.#sock = createSocket();
        this.#sock.ev.on("creds.update", saveCreds);
        if (this.#config.onSocketCreated) {
          try {
            this.#config.onSocketCreated(this.#sock);
          } catch (err) {
            console.error("[VoipClient] Error in onSocketCreated:", err);
          }
        }

        process.removeAllListeners("uncaughtException");
        process.on("uncaughtException", (err: any) => {
          const code = err?.output?.statusCode ?? err?.data?.attrs?.code;
          const isLoggedOut = code === DisconnectReason?.loggedOut;
          if (!opened && !isLoggedOut && retries < maxRetries) {
            retries += 1;
            setTimeout(connectSocket, 1500);
          } else if (!opened) {
            rejectOpen(err);
          }
        });

        this.#sock.ev.on("connection.update", (update: any) => {
          if (update.qr) {
            retries = 0; // Reset retries as QR was actively generated
            if (this.#config.onQrCode) {
              try {
                this.#config.onQrCode(update.qr);
              } catch {}
            }
            if (!this.#config.silent) {
              void import("qrcode-terminal")
                .then((qrt) => (qrt.default ?? qrt).generate(update.qr, { small: true }))
                .catch(() => {
                  console.log("Scan this QR code in WhatsApp > Linked Devices:");
                  console.log(update.qr);
                });
            }
          }
          if (update.connection === "open") {
            opened = true;
            process.removeAllListeners("uncaughtException");
            resolveOpen();
            return;
          }
          if (update.connection === "close" && !opened) {
            const statusCode = update.lastDisconnect?.error?.output?.statusCode;
            const isLoggedOut = statusCode === DisconnectReason?.loggedOut;
            const shouldReconnect = !isLoggedOut && retries < maxRetries;
            if (shouldReconnect) {
              retries += 1;
              setTimeout(connectSocket, 1500);
            } else {
              rejectOpen(update.lastDisconnect?.error ?? new Error("socket closed before open"));
            }
          }
        });
      };

      connectSocket();
    });

    this.#signaling = new SignalingBridge({
      sock: this.#sock,
      onCallTerminated: (callId: string, reason: string) => {
        if (this.#activeCall && (!callId || this.#activeCall.callId === callId)) {
          this.#activeCall._forceEnd(reason);
        }
      },
    });
    await this.#signaling.init();

    this.#relay = new RelayRtcTransport({
      onTransportMessage: (data, ip, port) => this.#engine?.handleOnTransportMessage(data, ip, port),
      onIceRtt: (rttMs, ip, port) => this.#engine?.updateIceRtt(rttMs, ip, port),
    });

    this.#engine = new WasmEngine({
      callbacks: {
        onSignalingXmpp: (peerJid, callId, xmlPayload) =>
          this.#signaling!.sendSignaling(peerJid, callId, xmlPayload),
        onCallEvent: (eventType, eventData) => this.#handleCallEvent(eventType, eventData),
        sendDataToRelay: (data, ip, port) => this.#relay!.send(data, ip, port),
        onAudioCaptureInit: (config) => this.#handleAudioCaptureInit(config),
        onAudioCaptureStart: () => this.#handleAudioCaptureStart(),
        onAudioCaptureStop: () => this.#handleAudioCaptureStop(),
        onAudioPlaybackData: (audioData) => this.#activeCall?._emitAudio(audioData),
        cryptoHkdf: computeHkdf,
        hmacSha256: computeHmacSha256,
      },
    });

    await this.#engine.initialize();
    this.#signaling.attachEngine(this.#engine);

    const selfPnJid = this.#sock.authState.creds.me?.id;
    const selfLidJid = this.#sock.authState.creds.me?.lid;
    this.#engine.initVoipStack(selfPnJid, toBareJid(selfPnJid), selfLidJid);
    await this.#engine.waitForVoipStackReady();
    try { this.#engine.updateNetworkMedium(2, 0); } catch {}

    this.#sock.ws.on("CB:call", (node: any) => {
      this.#signaling!.processIncomingCall(node, this.#engine!, this.#activeCall?.callId ?? "");
    });
    this.#sock.ws.on("CB:receipt", (node: any) => {
      if (!isCallReceiptNode(node)) return;
      this.#signaling!.processIncomingReceipt(node, this.#engine!, this.#activeCall?.callId ?? "");
      const child = Array.isArray(node.content) ? node.content[0] : null;
      if (node.attrs?.type === "terminate" || child?.attrs?.type === "terminate") {
        if (this.#activeCall) {
          const endingCall = this.#activeCall;
          this.#activeCall = null;
          endingCall._forceEnd("remote_ended");
        }
      }
    });

    this.#sock.ev.on("call", (callEvents: any[]) => {
      if (!Array.isArray(callEvents)) return;
      for (const ev of callEvents) {
        const callId = ev.id || ev.callId;
        const status = ev.status;
        if (this.#activeCall && (!callId || this.#activeCall.callId === callId)) {
          if (status === "reject" || status === "timeout") {
            const endingCall = this.#activeCall;
            this.#activeCall = null;
            endingCall._forceEnd(status);
          }
        }
      }
    });
  };

  /** Place an outbound voice call. */
  call = async (
    phoneNumber: string,
    opts: { audioSource?: string; durationMs?: number } = {},
  ): Promise<ActiveCall> => {
    if (!this.#engine || !this.#signaling) throw new Error("Not connected. Call connect() first.");
    if (this.#activeCall) throw new Error("A call is already active.");

    let targetNumber = phoneNumber.replace(/\D/g, "");
    if (targetNumber.length === 10 && /^[6-9]/.test(targetNumber)) {
      targetNumber = `91${targetNumber}`;
    }
    const targetPnJid = `${targetNumber}@s.whatsapp.net`;
    const durationMs = opts.durationMs ?? 120_000;
    const audioSource = opts.audioSource ?? "silence";

    const peerLid = await this.#signaling.resolveLid(targetPnJid);
    if (!peerLid) throw new Error(`Could not resolve LID for ${targetPnJid}`);

    for (const jid of [targetPnJid, peerLid]) {
      try { await this.#sock.presenceSubscribe(jid); } catch {}
    }
    await new Promise((r) => setTimeout(r, 750));

    const peerDeviceJids = await this.#signaling.discoverPeerDevices(peerLid);
    const deviceList = peerDeviceJids.length ? peerDeviceJids : [toBareJid(peerLid)];

    await this.#signaling.ensureSessionsForPeers(deviceList);

    await new Promise((r) => setTimeout(r, 500));
    await this.#signaling.issueTcToken(peerLid);
    const tcToken = await this.#signaling.ensureTcToken(peerLid, targetPnJid);

    const callId = ("00" + randomBytes(16).toString("hex").slice(2)).toUpperCase();

    const call = new ActiveCall(callId, this.#engine, durationMs);
    call._audioSource = audioSource;
    this.#activeCall = call;

    const cleanupActiveCall = () => {
      if (this.#activeCall === call) {
        this.#activeCall = null;
      }
      this.#handleAudioCaptureStop();
    };
    call.once("ended", cleanupActiveCall);

    this.#engine.startCall({
      peerJid: peerLid,
      peerPn: targetPnJid,
      peerList: deviceList,
      callId,
      isVideo: false,
      isLidCall: true,
      isFromDialer: false,
      extraData: tcToken,
    });

    return call;
  };

  /** Tear down the WhatsApp socket and release resources. */
  disconnect = (): void => {
    if (this.#activeCall) {
      const call = this.#activeCall;
      this.#activeCall = null;
      call._forceEnd("disconnect");
    }
    this.#handleAudioCaptureStop();
    this.#relay?.closeAll();
    this.#engine?.destroy();
    this.#sock?.end?.();
    this.#engine = null;
    this.#relay = null;
    this.#signaling = null;
    this.#sock = null;
  };

  // ─── private ──────────────────────────────────────────────────────────────

  #handleCallEvent = (eventType: number, eventData?: string): void => {
    if (eventType === 16 && eventData) {
      try {
        const parsed = JSON.parse(eventData);
        const info = parsed.call_info ?? parsed.callInfo ?? {};
        const callState = Number(info.call_state ?? info.callState ?? 0);
        this.#activeCall?._updateState(callState);
      } catch {}
    } else if (eventType === 156 && eventData) {
      try {
        const update = JSON.parse(eventData) as RelayListUpdatePayload;
        this.#relay?.updateRelayList(update);
      } catch {}
    } else if (eventType === 2) {
      if (this.#activeCall) {
        const endingCall = this.#activeCall;
        this.#activeCall = null;
        endingCall._forceEnd("remote_end");
      }
    }
  };

  #handleAudioCaptureInit = (config: {
    sampleRate: number; channels: number; bitsPerSample: number; framesPerChunk: number;
  }): void => {
    if (!this.#engine) return;
    this.#captureSampleRate = config.sampleRate || 16000;
    this.#captureChannels = config.channels || 1;
    this.#captureFramesPerChunk = config.framesPerChunk || 320;
    const chunkSamples = this.#captureFramesPerChunk * this.#captureChannels;
    this.#captureChunkBytes = chunkSamples * Float32Array.BYTES_PER_ELEMENT;
    this.#capturePtr = this.#engine.malloc(this.#captureChunkBytes);
  };

  #handleAudioCaptureStart = (): void => {
    if (!this.#engine || !this.#capturePtr) return;
    const audioSource = this.#activeCall?._audioSource ?? "silence";
    this.#feeder = new AudioFeeder(
      this.#captureSampleRate,
      this.#captureChannels,
      this.#captureFramesPerChunk,
      (chunk) => {
        if (this.#engine && this.#capturePtr) this.#engine.sendAudioData(chunk, this.#capturePtr);
      },
      audioSource,
    );
    if (this.#activeCall) {
      this.#activeCall._attachFeeder(this.#feeder);
    }
    this.#feeder.start();
  };

  #handleAudioCaptureStop = (): void => {
    if (this.#activeCall) {
      this.#activeCall._feeder = null;
    }
    this.#feeder?.stop();
    this.#feeder = null;
    if (this.#engine && this.#capturePtr) {
      try { this.#engine.free(this.#capturePtr); } catch {}
      this.#capturePtr = 0;
    }
  };

  get socket(): any {
    return this.#sock;
  }
}

