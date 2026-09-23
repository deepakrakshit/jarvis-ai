import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { ActiveCall, CallState } from "../src/voip/index.mjs";
import { CallSession } from "../src/whatsapp/call.js";

describe("Call Lifecycle and State Machine", () => {
  const createMockEngine = () => ({
    endCall: () => {},
    setMute: () => {},
  });

  test("progresses through lifecycle states: Idle -> Ringing -> Connected -> Ended", async () => {
    const mockEngine = createMockEngine();
    const activeCall = new ActiveCall("CALL_TEST_1", mockEngine as any, 10000);
    const session = new CallSession(activeCall, "917088669912");

    assert.equal(session.state, CallState.Idle);

    let ringingFired = false;
    let connectedFired = false;
    let endedFired = false;

    session.on("ringing", () => {
      ringingFired = true;
    });
    session.on("connected", () => {
      connectedFired = true;
    });
    session.on("ended", () => {
      endedFired = true;
    });

    // Simulate WASM signaling ringing (PreacceptReceived = 2)
    activeCall._updateState(CallState.PreacceptReceived);
    assert.equal(ringingFired, true);

    // Simulate WASM signaling connected (Active = 6)
    activeCall._updateState(CallState.Active);
    assert.equal(connectedFired, true);

    // Simulate local hangup
    activeCall._forceEnd("local_ended");
    assert.equal(endedFired, true);
    assert.equal(activeCall.state, CallState.Idle);

    const endReason = await session.waitForEnd();
    assert.equal(endReason, "local_ended");

    const metrics = session.getMetrics();
    assert.equal(metrics.targetNumber, "917088669912");
    assert.equal(metrics.endReason, "local_ended");
    assert.equal(metrics.state, "ended");
  });

  test("handles sequential calls idempotently without state collision", async () => {
    const mockEngine = createMockEngine();

    // Call 1
    const call1 = new ActiveCall("CALL_1", mockEngine as any, 5000);
    assert.equal(call1.state, CallState.Idle);
    call1._updateState(CallState.Active);
    assert.equal(call1.state, CallState.Active);
    call1._forceEnd("completed");
    const reason1 = await call1.waitForEnd();
    assert.equal(reason1, "completed");
    assert.equal(call1.state, CallState.Idle);

    // Call 2 (Sequential)
    const call2 = new ActiveCall("CALL_2", mockEngine as any, 5000);
    assert.equal(call2.state, CallState.Idle);
    call2._updateState(CallState.Active);
    assert.equal(call2.state, CallState.Active);
    call2._forceEnd("completed");
    const reason2 = await call2.waitForEnd();
    assert.equal(reason2, "completed");
    assert.equal(call2.state, CallState.Idle);
  });

  test("audio queue and barge-in clearing operates cleanly", () => {
    const mockEngine = createMockEngine();
    const activeCall = new ActiveCall("CALL_QUEUE_TEST", mockEngine as any, 5000);
    const session = new CallSession(activeCall, "917088669912");

    const chunk = new Float32Array(320).fill(0.5);
    session.pushAudio(chunk);
    session.pushAudio(chunk);

    assert.equal(activeCall.getAudioQueueLength(), 2);

    session.clearAudioQueue();
    assert.equal(activeCall.getAudioQueueLength(), 0);

    const metrics = session.getMetrics();
    assert.equal(metrics.interruptionDiscards, 1);
    assert.equal(metrics.outboundFramesPushed, 2);
    assert.equal(metrics.outboundSamplesPushed, 640);
  });
});
