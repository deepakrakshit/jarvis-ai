/**
 * Audio abstraction and loopback diagnostics bridge for WhatsApp voice calls.
 *
 * Provides PCM frame streaming interfaces and diagnostics to measure
 * roundtrip latency, frame jitter, and packet loss.
 */
import { EventEmitter } from "node:events";
import type { AudioLoopbackDiagnostics, WhatsAppAudioInput, WhatsAppAudioOutput } from "./types.js";
export declare class WhatsAppAudioInputStream extends EventEmitter implements WhatsAppAudioInput {
    private isClosed;
    private queue;
    push(chunk: Buffer): void;
    read(size?: number): Promise<Buffer | null>;
    close(): void;
}
export declare class WhatsAppAudioOutputStream implements WhatsAppAudioOutput {
    private isClosed;
    private onWriteChunk?;
    constructor(onWriteChunk?: (chunk: Buffer) => void);
    write(chunk: Buffer): Promise<boolean>;
    flush(): Promise<void>;
    close(): void;
}
export declare class WhatsAppAudioBridge {
    readonly input: WhatsAppAudioInputStream;
    readonly output: WhatsAppAudioOutputStream;
    constructor();
    /**
     * Run synthetic audio loopback diagnostics.
     * Sends 50 20ms PCM audio frames, measures delivery, latency, and packet loss.
     */
    runLoopbackDiagnostics(frameCount?: number): Promise<AudioLoopbackDiagnostics>;
}
