import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  isSensitiveCryptographicString,
  isSensitiveSessionObject,
  sanitizeArgument,
  installSecuritySanitizer,
  uninstallSecuritySanitizer,
} from "../src/security/sanitizer.js";

describe("Security Sanitizer", () => {
  beforeEach(() => {
    uninstallSecuritySanitizer();
  });

  afterEach(() => {
    uninstallSecuritySanitizer();
  });

  test("detects sensitive cryptographic strings", () => {
    const leakStr = "Closing session: SessionEntry { privKey: <Buffer 18 24>, rootKey: <Buffer 05 69> }";
    assert.equal(isSensitiveCryptographicString(leakStr), true);

    const normalStr = "[JARVIS] WhatsApp client connected and standing by.";
    assert.equal(isSensitiveCryptographicString(normalStr), false);
  });

  test("detects sensitive session objects", () => {
    const sensitiveObj = {
      privKey: Buffer.from([1, 2, 3]),
      rootKey: Buffer.from([4, 5, 6]),
      registrationId: 2461,
    };
    assert.equal(isSensitiveSessionObject(sensitiveObj), true);

    const safeObj = {
      callId: "12345",
      status: "connected",
    };
    assert.equal(isSensitiveSessionObject(safeObj), false);
  });

  test("sanitizes SessionEntry objects and strings", () => {
    const sanitizedObj = sanitizeArgument({
      privKey: Buffer.from([1]),
      rootKey: Buffer.from([2]),
    });
    assert.equal(sanitizedObj, "[WhatsApp] Signal session state rotated.");

    const leakStr = "Closing session: SessionEntry { ... }";
    const sanitizedStr = sanitizeArgument(leakStr);
    assert.equal(sanitizedStr, "[WhatsApp] Signal session state rotated.");
  });

  test("suppresses harmless Emscripten main-thread warning", () => {
    const emscriptenWarning = "Blocking on the main thread is very dangerous, see https://emscripten.org/docs/porting/pthreads.html#blocking-on-the-main-browser-thread";
    const result = sanitizeArgument(emscriptenWarning);
    assert.equal(result, "");
  });

  test("preserves regular messages untouched", () => {
    const regular = "[JARVIS] Good evening, Deepak.";
    const result = sanitizeArgument(regular);
    assert.equal(result, regular);
  });

  test("intercepts console.info and scrubs SessionEntry", () => {
    const logged: any[] = [];
    const origInfo = console.info;
    console.info = (...args: any[]) => {
      logged.push(args);
    };

    try {
      installSecuritySanitizer();
      console.info("Closing session:", { privKey: Buffer.from([1, 2, 3]) });
      assert.equal(logged.length, 1);
      assert.equal(logged[0][0], "[WhatsApp] Signal session state rotated.");
      assert.equal(logged[0][1], "[WhatsApp] Signal session state rotated.");
    } finally {
      uninstallSecuritySanitizer();
      console.info = origInfo;
    }
  });
});
