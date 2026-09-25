import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadConfig } from "../src/config.js";
import { CallHistoryManager, type CallHistoryEntry } from "../src/memory/call-history.js";
import { ContactResolver } from "../src/contacts/resolver.js";
import { ToolExecutor } from "../src/brain/executor.js";
import { generateCallReport } from "../src/chat/reporter.js";
import { ConsoleAgentBrain } from "../src/brain/agent-brain.js";
import { resolve } from "node:path";
import { existsSync, unlinkSync } from "node:fs";

const SCRATCH_FILE = resolve("./tests/scratch/test-fallback-history.json");

describe("Gemini 3.8 Live Brain & Single Fallback Policy", () => {
  test("1. primary model defaults to gemini-3.8-live and fallback to gemini-3.5-flash-lite", () => {
    const config = loadConfig();
    assert.equal(config.geminiModel, "gemini-3.8-live");
    assert.equal(config.fallbackModel, "gemini-3.5-flash-lite");
  });

  test("2. generateCallReport emits exact required fallback diagnostics on Live limitation", async () => {
    const logs: string[] = [];
    const originalLog = console.log;
    console.log = (...args: any[]) => {
      logs.push(args.join(" "));
      originalLog(...args);
    };

    try {
      const transcript = [
        { role: "assistant" as const, text: "Kya aap kal hackathon me aayenge?", timestamp: 1 },
        { role: "user" as const, text: "Haan bilkul main aunga.", timestamp: 2 },
      ];
      const metrics = {
        durationMs: 12000,
        inboundFramesCount: 600,
        outboundFramesPushed: 600,
        interruptionDiscards: 0,
        endReason: "completed",
      };

      // Pass an invalid API key / mock model to trigger fallback path
      const report = await generateCallReport(
        "INVALID_MOCK_KEY_TRIGGER_FALLBACK",
        "Deepak",
        "Rahul",
        "919876543210",
        transcript,
        metrics,
        "gemini-3.8-live",
        "gemini-3.5-flash-lite"
      );

      // Verify the fallback diagnostics were emitted
      const hasLacksCapability = logs.some((l) =>
        l.includes("[Model Fallback] Gemini 3.8 Live lacks required capability:")
      );
      const hasUsingFallback = logs.some((l) =>
        l.includes("[Model Fallback] Using Gemini 3.5 Flash Lite for this specific operation.")
      );

      assert.equal(hasLacksCapability, true);
      assert.equal(hasUsingFallback, true);
      assert.match(report.recipientReply, /Haan bilkul/);
    } finally {
      console.log = originalLog;
    }
  });

  test("3. execute_terminal blocks restricted destructive keywords", async () => {
    if (existsSync(SCRATCH_FILE)) {
      try { unlinkSync(SCRATCH_FILE); } catch {}
    }
    const historyManager = new CallHistoryManager(SCRATCH_FILE);
    const contactResolver = new ContactResolver();
    const executor = new ToolExecutor({
      historyManager,
      contactResolver,
      getActiveCall: () => null,
      startOutboundCall: async () => ({ callId: "1", target: "1", outcome: "ok" }),
      endActiveCall: () => false,
    });

    const blockedCommands = [
      "rm -rf /",
      "del /f /q file.txt",
      "format C:",
      "cat .env",
      "echo $API_KEY",
    ];

    for (const cmd of blockedCommands) {
      const result = await executor.execute("execute_terminal", { command: cmd });
      assert.equal(result.success, false);
      assert.match(result.error || "", /Security Policy Violation/i);
    }
  });

  test("4. execute_terminal permits safe inspection commands", async () => {
    if (existsSync(SCRATCH_FILE)) {
      try { unlinkSync(SCRATCH_FILE); } catch {}
    }
    const historyManager = new CallHistoryManager(SCRATCH_FILE);
    const contactResolver = new ContactResolver();
    const executor = new ToolExecutor({
      historyManager,
      contactResolver,
      getActiveCall: () => null,
      startOutboundCall: async () => ({ callId: "1", target: "1", outcome: "ok" }),
      endActiveCall: () => false,
    });

    const result = await executor.execute("execute_terminal", { command: "node -v" });
    assert.equal(result.success, true);
    assert.match(result.result.stdout, /v\d+\./);
  });

  test("5. save_call_record tool updates latest call history record", async () => {
    if (existsSync(SCRATCH_FILE)) {
      try { unlinkSync(SCRATCH_FILE); } catch {}
    }
    const historyManager = new CallHistoryManager(SCRATCH_FILE);
    const initialRecord: CallHistoryEntry = {
      callId: "CALL_DEBRIEF_TEST",
      targetName: "Rahul",
      phoneNumber: "919876543210",
      objective: "Discuss party",
      callOutcome: "Connected",
      recipientReply: "Initial text",
      summary: "Initial summary",
      transcript: [],
      durationSec: "10.0",
      timestamp: Date.now(),
    };
    await historyManager.addRecord(initialRecord);

    const contactResolver = new ContactResolver();
    const executor = new ToolExecutor({
      historyManager,
      contactResolver,
      getActiveCall: () => null,
      startOutboundCall: async () => ({ callId: "1", target: "1", outcome: "ok" }),
      endActiveCall: () => false,
    });

    const result = await executor.execute("save_call_record", {
      conversationalMessage: "Sir, Rahul confirmed attendance.",
      recipientReply: "Main pakka aunga agar party milegi.",
      callOutcome: "Confirmed with conditions",
      summary: "Rahul confirmed on party condition.",
      conditions: ["Demanded party"],
      commitments: ["Will attend"],
      actionItems: ["Plan party after hackathon"],
    });

    assert.equal(result.success, true);
    const updated = historyManager.getLatest();
    assert.equal(updated?.recipientReply, "Main pakka aunga agar party milegi.");
    assert.equal(updated?.callOutcome, "Confirmed with conditions");
    assert.deepEqual(updated?.conditions, ["Demanded party"]);

    if (existsSync(SCRATCH_FILE)) {
      try { unlinkSync(SCRATCH_FILE); } catch {}
    }
  });

  test("6. ConsoleAgentBrain emits exact fallback diagnostics when Live API fails", async () => {
    const logs: string[] = [];
    const originalLog = console.log;
    console.log = (...args: any[]) => {
      logs.push(args.join(" "));
      originalLog(...args);
    };

    try {
      const config = loadConfig();
      const brokenConfig = {
        ...config,
        geminiApiKey: "INVALID_TEST_KEY_FOR_FALLBACK",
      };

      const historyManager = new CallHistoryManager(SCRATCH_FILE);
      const contactResolver = new ContactResolver();
      const executor = new ToolExecutor({
        historyManager,
        contactResolver,
        getActiveCall: () => null,
        startOutboundCall: async () => ({ callId: "1", target: "1", outcome: "ok" }),
        endActiveCall: () => false,
      });

      const brain = new ConsoleAgentBrain({
        config: brokenConfig,
        executor,
      });

      try {
        await brain.processUserMessage("hello");
      } catch {
        // Fallback generateContent with invalid key will also throw
      }

      const hasLacksCapability = logs.some((l) =>
        l.includes("[Model Fallback] Gemini 3.8 Live lacks required capability:")
      );
      const hasUsingFallback = logs.some((l) =>
        l.includes("[Model Fallback] Using Gemini 3.5 Flash Lite for this specific operation.")
      );

      assert.equal(hasLacksCapability, true);
      assert.equal(hasUsingFallback, true);
    } finally {
      console.log = originalLog;
      if (existsSync(SCRATCH_FILE)) {
        try { unlinkSync(SCRATCH_FILE); } catch {}
      }
    }
  });
});

