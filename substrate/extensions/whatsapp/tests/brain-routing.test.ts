import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { existsSync, unlinkSync } from "node:fs";
import { loadConfig } from "../src/config.js";
import { CallHistoryManager, type CallHistoryEntry } from "../src/memory/call-history.js";
import { ContactResolver } from "../src/contacts/resolver.js";
import { ToolExecutor } from "../src/brain/executor.js";
import { ConsoleAgentBrain } from "../src/brain/agent-brain.js";

const SCRATCH_HISTORY_FILE = resolve("./tests/scratch/test-brain-history.json");

let lastLiveApiCall = 0;
async function throttleLiveApi(): Promise<void> {
  if (process.env.RUN_LIVE_TESTS !== "true") return;
  const now = Date.now();
  const elapsed = now - lastLiveApiCall;
  if (lastLiveApiCall > 0 && elapsed < 13000) {
    await new Promise((r) => setTimeout(r, 13000 - elapsed));
  }
  lastLiveApiCall = Date.now();
}

function createMockAiClient() {
  return {
    models: {
      async generateContent(params: any) {
        const turns = params.contents;
        const lastTurn = turns[turns.length - 1];

        // 1. User turn: simulate Gemini reasoning to select tools
        if (lastTurn.role === "user") {
          const userText = String(lastTurn.parts[0]?.text || "").toLowerCase();

          // A. Retrieval queries
          if (
            userText.includes("usne kya bola") ||
            userText.includes("inform to kro") ||
            userText.includes("what did he say") ||
            userText.includes("condition")
          ) {
            return {
              candidates: [
                {
                  content: {
                    role: "model",
                    parts: [{ functionCall: { name: "get_latest_call", args: {} } }],
                  },
                },
              ],
              functionCalls: [{ name: "get_latest_call", args: {} }],
            };
          }

          if (userText.includes("summary")) {
            return {
              candidates: [
                {
                  content: {
                    role: "model",
                    parts: [{ functionCall: { name: "get_call_summary", args: {} } }],
                  },
                },
              ],
              functionCalls: [{ name: "get_call_summary", args: {} }],
            };
          }

          // B. Call intent with pronoun / reference resolution:
          if (
            userText.includes("call him again") ||
            userText.includes("same number") ||
            userText.includes("tell him deepak agreed")
          ) {
            return {
              candidates: [
                {
                  content: {
                    role: "model",
                    parts: [{ functionCall: { name: "get_latest_call", args: {} } }],
                  },
                },
              ],
              functionCalls: [{ name: "get_latest_call", args: {} }],
            };
          }

          return {
            text: "Hello Deepak Sir, how may I assist you today?",
          };
        }

        // 2. Tool response turn: Gemini reasons over tool output
        if (lastTurn.role === "tool") {
          const fnResp = lastTurn.parts[0]?.functionResponse;
          const output = fnResp?.response?.output;
          const userTurn = turns.find((t: any) => t.role === "user");
          const userText = String(userTurn?.parts[0]?.text || "").toLowerCase();

          // If the user's intent was to place a call:
          if (
            userText.includes("call him again") ||
            userText.includes("same number") ||
            userText.includes("tell him deepak agreed")
          ) {
            const alreadyCalled = turns.some(
              (t: any) =>
                t.role === "model" &&
                t.parts?.some?.((p: any) => p?.functionCall?.name === "call_whatsapp")
            );

            if (!alreadyCalled) {
              const target = output?.phoneNumber || output?.targetName || "919876543210";
              const objective = userText.includes("meeting postponed")
                ? "Inform recipient that meeting is postponed"
                : userText.includes("party hackathon")
                ? "Tell recipient that Deepak agreed, but party will be after winning"
                : "Follow up call on behalf of Deepak";

              return {
                candidates: [
                  {
                    content: {
                      role: "model",
                      parts: [
                        {
                          functionCall: {
                            name: "call_whatsapp",
                            args: { target, objective },
                          },
                        },
                      ],
                    },
                  },
                ],
                functionCalls: [
                  {
                    name: "call_whatsapp",
                    args: { target, objective },
                  },
                ],
              };
            }

            return {
              text: "Sir, I have placed the WhatsApp call and will report back once completed.",
            };
          }

          // If retrieval intent, produce natural grounded response based strictly on tool facts
          const reply = output?.recipientReply || output?.summary || "No response recorded.";
          return {
            text: `Sir, according to call records, they stated: "${reply}".`,
          };
        }

        return {
          text: "Sir, your request has been processed.",
        };
      },
    },
  };
}

describe("Agent Brain & Tool Routing", () => {
  let config = loadConfig();
  let historyManager: CallHistoryManager;
  let contactResolver: ContactResolver;
  let activeCallMock: any = null;
  let outboundCallLog: any[] = [];

  beforeEach(() => {
    if (existsSync(SCRATCH_HISTORY_FILE)) {
      try { unlinkSync(SCRATCH_HISTORY_FILE); } catch {}
    }
    historyManager = new CallHistoryManager(SCRATCH_HISTORY_FILE);
    contactResolver = new ContactResolver({ defaultCountryCode: "91" });
    contactResolver.registerContact("Rahul", "9876543210");
    contactResolver.registerContact("Priya", "7088669912");
    activeCallMock = null;
    outboundCallLog = [];
  });

  afterEach(() => {
    if (existsSync(SCRATCH_HISTORY_FILE)) {
      try { unlinkSync(SCRATCH_HISTORY_FILE); } catch {}
    }
  });

  const createExecutor = () => {
    return new ToolExecutor({
      historyManager,
      contactResolver,
      getActiveCall: () => activeCallMock,
      startOutboundCall: async (params) => {
        outboundCallLog.push(params);
        return {
          callId: `CALL_OUT_${Date.now()}`,
          target: params.target,
          outcome: "Delivered",
        };
      },
      endActiveCall: () => {
        if (activeCallMock) {
          activeCallMock = null;
          return true;
        }
        return false;
      },
    });
  };

  const createBrain = (executor: ToolExecutor) => {
    return new ConsoleAgentBrain({
      config,
      executor,
      aiClient: process.env.RUN_LIVE_TESTS === "true" ? undefined : createMockAiClient(),
    });
  };

  // ─── DETERMINISTIC SAFETY & PROVENANCE TESTS ─────────────────────────────

  test("1. active call must block second call deterministically", async () => {
    activeCallMock = { state: 6, callId: "CALL_EXISTING_1", targetNumber: "917088669912" };
    const executor = createExecutor();

    const result = await executor.execute("call_whatsapp", {
      target: "9876543210",
      objective: "Test call",
    });

    assert.equal(result.success, false);
    assert.match(result.error || "", /already active/i);
    assert.equal(outboundCallLog.length, 0);
  });

  test("2. post-call state must become IDLE and subsequent call succeeds", async () => {
    activeCallMock = { state: 6, callId: "CALL_1", targetNumber: "917088669912" };
    const executor = createExecutor();

    // End active call
    const endResult = await executor.execute("end_current_call", {});
    assert.equal(endResult.success, true);
    assert.equal(endResult.result.ended, true);

    const stateResult = await executor.execute("get_current_call_state", {});
    assert.equal(stateResult.result.state, "IDLE");

    // Second call immediately succeeds
    const callResult = await executor.execute("call_whatsapp", {
      target: "9876543210",
      objective: "Follow-up call",
    });
    assert.equal(callResult.success, true);
    assert.equal(outboundCallLog.length, 1);
  });

  test("3. malformed summary or placeholder must NEVER become recipient fact", async () => {
    const executor = createExecutor();

    // Add a record with a malformed summary containing placeholder
    const recordWithPlaceholder: CallHistoryEntry = {
      callId: "CALL_BUG_CHECK",
      targetName: "Rahul",
      phoneNumber: "919876543210",
      objective: "Ask about hackathon",
      callOutcome: "Connected",
      recipientReply: "Please refer to verbatim transcript.",
      summary: "Please refer to verbatim transcript.",
      transcript: [
        { role: "assistant", text: "Are you joining the hackathon?", timestamp: 1 },
        { role: "user", text: "Yes I will join if you give party first.", timestamp: 2 },
      ],
      durationSec: "15.0",
      timestamp: Date.now(),
    };
    await historyManager.addRecord(recordWithPlaceholder);

    const latest = await executor.execute("get_latest_call", {});
    assert.equal(latest.success, true);
    // Provenance engine must have extracted the real user turn instead of the placeholder
    assert.equal(latest.result.recipientReply, "Yes I will join if you give party first.");
    assert.equal(latest.result.recipientReply.includes("Please refer"), false);
  });

  test("4. unavailable history must produce honest response without fabrication", async () => {
    const executor = createExecutor();
    const result = await executor.execute("get_latest_call", {});
    assert.equal(result.success, true);
    assert.equal(result.result.status, "empty");
    assert.match(result.result.message, /no completed call/i);
  });

  test("5. explicit new number must override previous target", async () => {
    const executor = createExecutor();
    await historyManager.addRecord({
      callId: "CALL_OLD",
      targetName: "Rahul",
      phoneNumber: "919876543210",
      objective: "Old call",
      callOutcome: "Done",
      recipientReply: "Okay",
      summary: "Done",
      transcript: [],
      durationSec: "10.0",
      timestamp: Date.now(),
    });

    const callResult = await executor.execute("call_whatsapp", {
      target: "7088669912",
      objective: "New person call",
    });

    assert.equal(callResult.success, true);
    assert.equal(outboundCallLog[0].target, "917088669912");
  });

  // ─── SEMANTIC REASONING & INTENT ROUTING TESTS WITH GEMINI ────────────────

  test("6. 'usne kya bola?' triggers retrieval and NO phone call", async () => {
    const executor = createExecutor();
    await historyManager.addRecord({
      callId: "CALL_101",
      targetName: "Rahul",
      phoneNumber: "919876543210",
      objective: "Ask about hackathon",
      callOutcome: "Agreed",
      recipientReply: "Haan main participate karunga but pehle party chahiye.",
      summary: "Rahul agreed to participate on the condition of a party.",
      transcript: [
        { role: "assistant", text: "Kya tum hackathon me join karoge?", timestamp: 1 },
        { role: "user", text: "Haan main participate karunga but pehle party chahiye.", timestamp: 2 },
      ],
      durationSec: "20.0",
      timestamp: Date.now(),
      conditions: ["Demanded party first"],
    });

    const brain = createBrain(executor);
    await throttleLiveApi();
    const result = await brain.processUserMessage("usne kya bola?");

    assert.ok(result.toolsInvoked.includes("get_latest_call") || result.toolsInvoked.includes("get_call_transcript"));
    assert.equal(result.toolsInvoked.includes("call_whatsapp"), false);
    assert.equal(outboundCallLog.length, 0); // ZERO phone calls placed!
    assert.match(result.response.toLowerCase(), /party|participate|kya/);
  });

  test("7. 'inform to kro ki uska response kya tha' triggers retrieval and NO phone call", async () => {
    const executor = createExecutor();
    await historyManager.addRecord({
      callId: "CALL_102",
      targetName: "Rahul",
      phoneNumber: "919876543210",
      objective: "Ask about hackathon",
      callOutcome: "Agreed",
      recipientReply: "I will join the hackathon.",
      summary: "Rahul confirmed hackathon participation.",
      transcript: [{ role: "user", text: "I will join the hackathon.", timestamp: 1 }],
      durationSec: "12.0",
      timestamp: Date.now(),
    });

    const brain = createBrain(executor);
    await throttleLiveApi();
    const result = await brain.processUserMessage("inform to kro ki uska response kya tha");

    assert.ok(result.toolsInvoked.includes("get_latest_call") || result.toolsInvoked.includes("get_call_summary"));
    assert.equal(result.toolsInvoked.includes("call_whatsapp"), false);
    assert.equal(outboundCallLog.length, 0); // ZERO calls placed!
  });

  test("8. 'what did he say?' triggers retrieval and NO phone call", async () => {
    const executor = createExecutor();
    await historyManager.addRecord({
      callId: "CALL_103",
      targetName: "Rahul",
      phoneNumber: "919876543210",
      objective: "Check attendance",
      callOutcome: "Confirmed",
      recipientReply: "I will arrive at 8 PM.",
      summary: "Rahul confirmed arrival at 8 PM.",
      transcript: [{ role: "user", text: "I will arrive at 8 PM.", timestamp: 1 }],
      durationSec: "15.0",
      timestamp: Date.now(),
    });

    const brain = createBrain(executor);
    await throttleLiveApi();
    const result = await brain.processUserMessage("what did he say?");

    assert.ok(result.toolsInvoked.includes("get_latest_call") || result.toolsInvoked.includes("get_call_summary"));
    assert.equal(result.toolsInvoked.includes("call_whatsapp"), false);
    assert.equal(outboundCallLog.length, 0);
  });

  test("9. 'previous call summary' triggers retrieval and NO phone call", async () => {
    const executor = createExecutor();
    await historyManager.addRecord({
      callId: "CALL_104",
      targetName: "Priya",
      phoneNumber: "917088669912",
      objective: "Project update",
      callOutcome: "Completed",
      recipientReply: "Project draft is ready.",
      summary: "Priya completed project draft.",
      transcript: [{ role: "user", text: "Project draft is ready.", timestamp: 1 }],
      durationSec: "18.0",
      timestamp: Date.now(),
    });

    const brain = createBrain(executor);
    await throttleLiveApi();
    const result = await brain.processUserMessage("previous call summary");

    assert.ok(result.toolsInvoked.includes("get_call_summary") || result.toolsInvoked.includes("get_latest_call"));
    assert.equal(result.toolsInvoked.includes("call_whatsapp"), false);
    assert.equal(outboundCallLog.length, 0);
  });

  test("10. 'what was the condition?' retrieves factual condition without calling", async () => {
    const executor = createExecutor();
    await historyManager.addRecord({
      callId: "CALL_105",
      targetName: "Rahul",
      phoneNumber: "919876543210",
      objective: "Ask about hackathon",
      callOutcome: "Condition stated",
      recipientReply: "I will join only if Deepak gives me a party first.",
      summary: "Rahul stated condition of a party before joining.",
      conditions: ["Demanded party first"],
      transcript: [{ role: "user", text: "I will join only if Deepak gives me a party first.", timestamp: 1 }],
      durationSec: "22.0",
      timestamp: Date.now(),
    });

    const brain = createBrain(executor);
    await throttleLiveApi();
    const result = await brain.processUserMessage("what was the condition?");

    assert.ok(result.toolsInvoked.includes("get_latest_call") || result.toolsInvoked.includes("get_call_summary"));
    assert.equal(result.toolsInvoked.includes("call_whatsapp"), false);
    assert.equal(outboundCallLog.length, 0);
    assert.match(result.response.toLowerCase(), /party/);
  });

  test("11. 'call him again' resolves previous target from memory and initiates call", async () => {
    const executor = createExecutor();
    await historyManager.addRecord({
      callId: "CALL_106",
      targetName: "Rahul",
      phoneNumber: "919876543210",
      objective: "First inquiry",
      callOutcome: "Done",
      recipientReply: "Sure",
      summary: "Inquiry done",
      transcript: [],
      durationSec: "15.0",
      timestamp: Date.now(),
    });

    const brain = createBrain(executor);
    await throttleLiveApi();
    const result = await brain.processUserMessage("call him again");

    assert.ok(result.toolsInvoked.includes("call_whatsapp"));
    assert.equal(outboundCallLog.length, 1);
    assert.equal(outboundCallLog[0].target, "919876543210");
  });

  test("12. 'same number pe call karo' resolves target from memory and calls", async () => {
    const executor = createExecutor();
    await historyManager.addRecord({
      callId: "CALL_107",
      targetName: "Priya",
      phoneNumber: "917088669912",
      objective: "Initial sync",
      callOutcome: "Done",
      recipientReply: "All good",
      summary: "Sync done",
      transcript: [],
      durationSec: "10.0",
      timestamp: Date.now(),
    });

    const brain = createBrain(executor);
    await throttleLiveApi();
    const result = await brain.processUserMessage("same number pe call karo aur bolo ki meeting postponed hai");

    assert.ok(result.toolsInvoked.includes("call_whatsapp"));
    assert.equal(outboundCallLog.length, 1);
    assert.equal(outboundCallLog[0].target, "917088669912");
  });

  test("13. 'tell him Deepak agreed' triggers follow-up call with previous target", async () => {
    const executor = createExecutor();
    await historyManager.addRecord({
      callId: "CALL_108",
      targetName: "Rahul",
      phoneNumber: "919876543210",
      objective: "Discuss party",
      callOutcome: "Condition stated",
      recipientReply: "Party first",
      summary: "Rahul asked for party",
      transcript: [],
      durationSec: "12.0",
      timestamp: Date.now(),
    });

    const brain = createBrain(executor);
    await throttleLiveApi();
    const result = await brain.processUserMessage("tell him Deepak agreed, but party hackathon jeetne ke baad hogi");

    assert.ok(result.toolsInvoked.includes("call_whatsapp"));
    assert.equal(outboundCallLog.length, 1);
    assert.equal(outboundCallLog[0].target, "919876543210");
  });
});
