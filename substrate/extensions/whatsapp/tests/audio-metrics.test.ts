import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { RealtimeAudioBridge } from "../src/bridge/realtime-audio.js";

describe("Audio Bridge Metrics & Farewell Policy", () => {
  const createMockCall = () => {
    const emitter = new EventEmitter() as any;
    emitter.callId = "MOCK_CALL_AUDIO";
    emitter.getMetrics = () => ({
      callId: "MOCK_CALL_AUDIO",
      targetNumber: "917088669912",
      durationMs: 15000,
      interruptionDiscards: 0,
      state: "connected",
    });
    emitter.clearAudioQueue = () => {};
    emitter.pushAudio = () => {};
    emitter.end = () => {
      emitter.emit("ended", "local_ended");
    };
    return emitter;
  };

  const createMockGemini = () => {
    const emitter = new EventEmitter() as any;
    emitter.isConnected = true;
    emitter.sendRealtimeAudio = () => {};
    return emitter;
  };

  test("tracks user speech turns and assistant turns in transcript", () => {
    const mockCall = createMockCall();
    const mockGemini = createMockGemini();
    const bridge = new RealtimeAudioBridge(mockCall, mockGemini, {
      logsDir: "./tests/scratch",
      farewellGracePeriodMs: 500,
    });

    bridge.start();

    // Model outputs greeting
    mockGemini.emit("outputTranscription", "Namaste Deepak sir ke behalf pe call hai.", true);

    // User replies
    mockGemini.emit("inputTranscription", "Haan bolo kya baat hai?", true);

    const transcript = bridge.getTranscript();
    assert.equal(transcript.length, 2);
    assert.equal(transcript[0].role, "assistant");
    assert.equal(transcript[0].text, "Namaste Deepak sir ke behalf pe call hai.");
    assert.equal(transcript[1].role, "user");
    assert.equal(transcript[1].text, "Haan bolo kya baat hai?");

    const metrics = bridge.getMetrics();
    assert.equal(metrics.userTurnCount, 1);

    bridge.stop();
  });

  test("tracks barge-in and increments interruptionCount", () => {
    const mockCall = createMockCall();
    const mockGemini = createMockGemini();
    const bridge = new RealtimeAudioBridge(mockCall, mockGemini, {
      logsDir: "./tests/scratch",
    });

    bridge.start();

    mockGemini.emit("interrupted");
    mockGemini.emit("interrupted");

    const metrics = bridge.getMetrics();
    assert.equal(metrics.interruptionCount, 2);
    assert.equal(metrics.bargeInCount, 2);
    assert.equal(metrics.generationInterruptedCount, 2);

    bridge.stop();
  });

  test("cancels farewell hangup if recipient speaks during grace period", async () => {
    const mockCall = createMockCall();
    const mockGemini = createMockGemini();
    let callEnded = false;
    mockCall.end = () => {
      callEnded = true;
    };

    const bridge = new RealtimeAudioBridge(mockCall, mockGemini, {
      logsDir: "./tests/scratch",
      farewellGracePeriodMs: 200,
    });

    bridge.start();

    // Assistant says goodbye
    mockGemini.emit("outputTranscription", "Theek hai main Deepak sir ko bata dunga, alvida!", true);

    // Recipient immediately re-engages within 50ms
    await new Promise((r) => setTimeout(r, 50));
    mockGemini.emit("inputTranscription", "Arrey ruko ek second!", true);

    // Wait beyond the original 200ms grace period
    await new Promise((r) => setTimeout(r, 250));

    // Call should NOT have ended because user re-engaged!
    assert.equal(callEnded, false);

    const metrics = bridge.getMetrics();
    assert.equal(metrics.postFarewellReengagementCount, 1);

    bridge.stop();
  });
});
