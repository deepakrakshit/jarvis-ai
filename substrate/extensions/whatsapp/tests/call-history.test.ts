import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { existsSync, unlinkSync } from "node:fs";
import { resolve } from "node:path";
import { CallHistoryManager, type CallHistoryEntry } from "../src/memory/call-history.js";

const TEST_HISTORY_FILE = resolve("./tests/scratch/test-call-history.json");

describe("Call History Manager", () => {
  afterEach(async () => {
    if (existsSync(TEST_HISTORY_FILE)) {
      try {
        unlinkSync(TEST_HISTORY_FILE);
      } catch {}
    }
  });

  const sampleRecord1: CallHistoryEntry = {
    callId: "CALL_001",
    targetName: "Rahul",
    phoneNumber: "919876543210",
    objective: "Ask about hackathon participation",
    callOutcome: "Confirmed participation",
    recipientReply: "Yes I will join the team",
    summary: "Rahul confirmed he is participating in Segue 3.0",
    transcript: [{ role: "user", text: "Yes I will join", timestamp: Date.now() }],
    durationSec: "35.2",
    timestamp: Date.now() - 10000,
  };

  const sampleRecord2: CallHistoryEntry = {
    callId: "CALL_002",
    targetName: "Priya",
    phoneNumber: "917088669912",
    objective: "Tell her I might miss tomorrow",
    callOutcome: "Message delivered",
    recipientReply: "Okay thanks for informing",
    summary: "Informed Priya about missing tomorrow's session",
    transcript: [{ role: "user", text: "Okay thanks", timestamp: Date.now() }],
    durationSec: "22.5",
    timestamp: Date.now(),
  };

  test("adds records and persists to disk", async () => {
    const manager = new CallHistoryManager(TEST_HISTORY_FILE);
    assert.equal(manager.count, 0);

    await manager.addRecord(sampleRecord1);
    assert.equal(manager.count, 1);
    assert.equal(existsSync(TEST_HISTORY_FILE), true);

    const latest = manager.getLatest();
    assert.equal(latest?.callId, "CALL_001");
    assert.equal(latest?.targetName, "Rahul");
  });

  test("reloads records across manager instances", async () => {
    const manager1 = new CallHistoryManager(TEST_HISTORY_FILE);
    await manager1.addRecord(sampleRecord1);
    await manager1.addRecord(sampleRecord2);

    // New instance reading same file
    const manager2 = new CallHistoryManager(TEST_HISTORY_FILE);
    assert.equal(manager2.count, 2);

    const latest = manager2.getLatest();
    assert.equal(latest?.callId, "CALL_002");
    assert.equal(latest?.targetName, "Priya");
  });

  test("finds latest record by target name or number", async () => {
    const manager = new CallHistoryManager(TEST_HISTORY_FILE);
    await manager.addRecord(sampleRecord1);
    await manager.addRecord(sampleRecord2);

    const byName = manager.getLatestByTarget("Rahul");
    assert.equal(byName?.phoneNumber, "919876543210");

    const byNumber = manager.getLatestByTarget("7088669912");
    assert.equal(byNumber?.targetName, "Priya");

    const notFound = manager.getLatestByTarget("NonExistent");
    assert.equal(notFound, null);
  });

  test("searches call history accurately", async () => {
    const manager = new CallHistoryManager(TEST_HISTORY_FILE);
    await manager.addRecord(sampleRecord1);
    await manager.addRecord(sampleRecord2);

    const results = manager.search("hackathon");
    assert.equal(results.length, 1);
    assert.equal(results[0].targetName, "Rahul");

    const searchNumber = manager.search("7088");
    assert.equal(searchNumber.length, 1);
    assert.equal(searchNumber[0].targetName, "Priya");
  });
});
