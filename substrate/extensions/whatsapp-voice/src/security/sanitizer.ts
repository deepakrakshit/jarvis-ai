/**
 * Security and Log Sanitizer.
 *
 * Prevents cryptographic leaks from libsignal, Baileys, and WASM runtime:
 * 1. Redacts raw Signal ratchet session keys (privKey, rootKey, baseKey, remoteIdentityKey, chainKey).
 * 2. Monkey-patches libsignal.SessionRecord to prevent leaking SessionEntry to console.info.
 * 3. Filters out the harmless Emscripten main-thread pthread warning cleanly.
 * 4. Intercepts console.* and process.stdout/stderr streams as defense-in-depth.
 */

import { inspect } from "node:util";

const SENSITIVE_PATTERNS = [
  /privKey/i,
  /rootKey/i,
  /baseKey/i,
  /remoteIdentityKey/i,
  /pendingPreKey/i,
  /chainKey/i,
  /SessionEntry/i,
  /_chains/i,
  /ephemeralKeyPair/i,
  /lastRemoteEphemeralKey/i,
];

const EMSCRIPTEN_PTHREAD_WARNING = "Blocking on the main thread is very dangerous, see https://emscripten.org/docs/porting/pthreads.html#blocking-on-the-main-browser-thread";

let installed = false;
let originalConsole: {
  log: typeof console.log;
  info: typeof console.info;
  warn: typeof console.warn;
  error: typeof console.error;
} | null = null;

let originalStdoutWrite: typeof process.stdout.write | null = null;
let originalStderrWrite: typeof process.stderr.write | null = null;

/**
 * Check if a string contains sensitive cryptographic keys.
 */
export function isSensitiveCryptographicString(str: string): boolean {
  return SENSITIVE_PATTERNS.some((pat) => pat.test(str));
}

/**
 * Check if an object or argument contains sensitive Signal session keys.
 */
export function isSensitiveSessionObject(obj: unknown): boolean {
  if (!obj || typeof obj !== "object") return false;
  try {
    const protoName = (obj as any).constructor?.name;
    if (protoName === "SessionEntry") return true;

    // Check specific fields
    const rec = obj as Record<string, unknown>;
    if ("privKey" in rec || "rootKey" in rec || "baseKey" in rec || "remoteIdentityKey" in rec || "_chains" in rec) {
      return true;
    }

    const inspected = inspect(obj, { depth: 2 });
    return SENSITIVE_PATTERNS.some((pat) => pat.test(inspected));
  } catch {
    return false;
  }
}

/**
 * Sanitize an individual argument.
 */
export function sanitizeArgument(arg: unknown): unknown {
  if (arg === null || arg === undefined) return arg;

  if (typeof arg === "string") {
    // Suppress harmless Emscripten pthread warning
    if (arg.includes(EMSCRIPTEN_PTHREAD_WARNING)) {
      return "";
    }

    if (arg.includes("Closing session:") || arg.includes("Opening session:")) {
      return "[WhatsApp] Signal session state rotated.";
    }

    if (isSensitiveCryptographicString(arg)) {
      return arg
        .replace(/privKey:\s*<Buffer[^>]*>/gi, "privKey: <Buffer [REDACTED]>")
        .replace(/rootKey:\s*<Buffer[^>]*>/gi, "rootKey: <Buffer [REDACTED]>")
        .replace(/baseKey:\s*<Buffer[^>]*>/gi, "baseKey: <Buffer [REDACTED]>")
        .replace(/remoteIdentityKey:\s*<Buffer[^>]*>/gi, "remoteIdentityKey: <Buffer [REDACTED]>")
        .replace(/chainKey:\s*\{[^}]*\}/gi, "chainKey: { [REDACTED] }")
        .replace(/pendingPreKey:\s*\{[^}]*\}/gi, "pendingPreKey: { [REDACTED] }");
    }
    return arg;
  }

  if (typeof arg === "object") {
    if (isSensitiveSessionObject(arg)) {
      return "[WhatsApp] Signal session state rotated.";
    }
  }

  return arg;
}

/**
 * Monkey-patch libsignal.SessionRecord to prevent it from dumping
 * SessionEntry objects to console.info/warn directly.
 */
export async function patchLibsignal(): Promise<void> {
  try {
    const libsignal = (await import("libsignal")) as any;
    const SessionRecord = libsignal?.SessionRecord || libsignal?.default?.SessionRecord;
    if (SessionRecord?.prototype) {
      SessionRecord.prototype.closeSession = function (session: any) {
        if (this.isClosed(session)) return;
        session.indexInfo.closed = Date.now();
      };

      SessionRecord.prototype.openSession = function (session: any) {
        session.indexInfo.closed = -1;
      };

      SessionRecord.prototype.removeOldSessions = function () {
        const CLOSED_SESSIONS_MAX = 40;
        while (Object.keys(this.sessions).length > CLOSED_SESSIONS_MAX) {
          let oldestSession: any = null;
          for (const key of Object.keys(this.sessions)) {
            const s = this.sessions[key];
            if (this.isClosed(s)) {
              if (!oldestSession || s.indexInfo.closed < oldestSession.indexInfo.closed) {
                oldestSession = s;
              }
            }
          }
          if (oldestSession) {
            delete this.sessions[oldestSession.indexInfo.baseKey];
          } else {
            break;
          }
        }
      };
    }
  } catch {
    // libsignal not present or already bundled
  }
}

/**
 * Install the security sanitizer globally on console and standard streams.
 */
export function installSecuritySanitizer(): void {
  if (installed) return;
  installed = true;

  originalConsole = {
    log: console.log,
    info: console.info,
    warn: console.warn,
    error: console.error,
  };

  originalStdoutWrite = process.stdout.write.bind(process.stdout);
  originalStderrWrite = process.stderr.write.bind(process.stderr);

  // Patch libsignal prototype asynchronously
  void patchLibsignal();

  const wrapConsoleMethod = (original: (...args: any[]) => void) => {
    return (...args: any[]) => {
      // Filter out empty arguments caused by suppressed warnings
      const sanitized = args
        .map(sanitizeArgument)
        .filter((a) => a !== "");

      if (sanitized.length === 0) return;
      original(...sanitized);
    };
  };

  console.log = wrapConsoleMethod(originalConsole.log);
  console.info = wrapConsoleMethod(originalConsole.info);
  console.warn = wrapConsoleMethod(originalConsole.warn);
  console.error = wrapConsoleMethod(originalConsole.error);

  // Wrap process.stdout.write and process.stderr.write
  const wrapStreamWrite = (original: any) => {
    return function (this: any, buffer: any, encodingOrCb?: any, cb?: any): boolean {
      const str = typeof buffer === "string" ? buffer : buffer?.toString ? buffer.toString("utf8") : "";

      // Filter out Emscripten pthread warning
      if (typeof str === "string" && str.includes(EMSCRIPTEN_PTHREAD_WARNING)) {
        if (typeof encodingOrCb === "function") encodingOrCb();
        if (typeof cb === "function") cb();
        return true;
      }

      if (typeof str === "string" && isSensitiveCryptographicString(str) && (str.includes("SessionEntry") || str.includes("privKey:"))) {
        const sanitized = "[WhatsApp Security] Signal session state rotated.\n";
        return original.call(this, sanitized, encodingOrCb, cb);
      }

      return original.call(this, buffer, encodingOrCb, cb);
    } as any;
  };

  process.stdout.write = wrapStreamWrite(originalStdoutWrite);
  process.stderr.write = wrapStreamWrite(originalStderrWrite);
}

/**
 * Uninstall the security sanitizer (primarily for test cleanup).
 */
export function uninstallSecuritySanitizer(): void {
  if (!installed) return;

  if (originalConsole) {
    console.log = originalConsole.log;
    console.info = originalConsole.info;
    console.warn = originalConsole.warn;
    console.error = originalConsole.error;
    originalConsole = null;
  }

  if (originalStdoutWrite) {
    process.stdout.write = originalStdoutWrite;
    originalStdoutWrite = null;
  }

  if (originalStderrWrite) {
    process.stderr.write = originalStderrWrite;
    originalStderrWrite = null;
  }

  installed = false;
}
