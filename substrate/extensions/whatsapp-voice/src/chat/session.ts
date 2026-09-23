/**
 * Interactive Terminal Voice & Text Chat Session with J.A.R.V.I.S.
 *
 * Provides a continuous, conversational AI console for the user with:
 * 1. Full Agent Brain reasoning layer for every semantic user request.
 * 2. Deterministic Tool Executor for retrieval, contact resolution, and VoIP.
 * 3. Strict provenance: raw transcripts are authoritative, never placeholders.
 * 4. Full duplex WebAssembly WhatsApp VoIP audio bridging with Gemini 3.8 Live.
 * 5. Detailed post-call debrief, chronological transcripts, and structured facts.
 */

import * as readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { loadConfig } from "../config.js";
import { WhatsAppManager } from "../whatsapp/client.js";
import { GeminiLiveSession } from "../gemini/live.js";
import { buildSystemPrompt } from "../gemini/prompts.js";
import { RealtimeAudioBridge } from "../bridge/realtime-audio.js";
import { generateCallReport } from "./reporter.js";
import { installSecuritySanitizer } from "../security/sanitizer.js";
import { CallHistoryManager, type CallHistoryEntry } from "../memory/call-history.js";
import { ContactResolver } from "../contacts/resolver.js";
import { ToolExecutor } from "../brain/executor.js";
import { ConsoleAgentBrain } from "../brain/agent-brain.js";
import { type CallSession } from "../whatsapp/call.js";

// Shield console and standard streams against cryptographic leaks
installSecuritySanitizer();

export class JarvisChatSession {
  readonly #config = loadConfig();
  readonly #callerName: string;
  readonly #assistantName: string;
  readonly #historyManager: CallHistoryManager;
  readonly #contactResolver: ContactResolver;
  readonly #brain: ConsoleAgentBrain;
  #whatsapp: WhatsAppManager | null = null;
  #rl: readline.Interface | null = null;
  #activeCallSession: CallSession | null = null;

  constructor() {
    this.#callerName = this.#config.userName;
    this.#assistantName = this.#config.assistantName;
    this.#historyManager = new CallHistoryManager(this.#config.callHistoryFile);
    this.#contactResolver = new ContactResolver({ defaultCountryCode: this.#config.defaultCountryCode });

    const executor = new ToolExecutor({
      historyManager: this.#historyManager,
      contactResolver: this.#contactResolver,
      getActiveCall: () => this.#activeCallSession,
      startOutboundCall: async (params) => {
        return await this.#executeOutboundCall({
          phoneNumber: params.target,
          targetName: "the recipient",
          objective: params.objective,
          conversationMode: params.conversationMode,
        });
      },
      endActiveCall: () => {
        if (this.#activeCallSession) {
          this.#activeCallSession.end();
          return true;
        }
        return false;
      },
    });

    this.#brain = new ConsoleAgentBrain({
      config: this.#config,
      executor,
    });
  }

  /**
   * Start the interactive JARVIS console session.
   */
  async start(): Promise<void> {
    this.#rl = readline.createInterface({ input, output });

    console.clear();
    console.log("========================================================================");
    console.log("            J.A.R.V.I.S. WHATSAPP AI VOICE CONSOLE                     ");
    console.log("========================================================================");
    console.log(` User:              ${this.#callerName}`);
    console.log(` Primary Brain:     Gemini 3.8 Live`);
    console.log(` Call Brain:        Gemini 3.8 Live`);
    console.log(` Post-Call Brain:   Gemini 3.8 Live`);
    console.log(` Fallback:          Gemini 3.5 Flash Lite (only when required)`);
    console.log(` Spoken Language:   Hinglish (Natural Hindi + English blend)`);
    console.log(` Voice Preset:      ${this.#config.voiceDefaultName}`);
    console.log(` Transport:         Pure WhatsApp Web VoIP WebAssembly`);
    console.log("========================================================================\n");

    // Initialize WhatsApp connection
    this.#whatsapp = new WhatsAppManager({ authDir: this.#config.whatsappAuthDir });

    if (this.#whatsapp.hasPersistedAuth) {
      console.log(`[${this.#assistantName}] Existing WhatsApp credentials located in ${this.#config.whatsappAuthDir}.`);
      console.log(`[${this.#assistantName}] Connecting to WhatsApp network...`);
    } else {
      console.log(`[${this.#assistantName}] No existing WhatsApp credentials found in ${this.#config.whatsappAuthDir}.`);
      console.log(`[${this.#assistantName}] Initializing QR link. Please scan the code with your phone:\n`);
    }

    try {
      await this.#whatsapp.connect();
      console.log(`\n[${this.#assistantName}] WhatsApp client connected and standing by.`);
      console.log(`[${this.#assistantName}] Good evening, ${this.#callerName}. All systems are active and initialized.`);
      console.log(`[${this.#assistantName}] You can speak or type your commands naturally anytime.\n`);
      console.log('Example: "call Rahul at 9876543210 and ask if he is participating in the hackathon"\n');
    } catch (err: any) {
      console.error(`[${this.#assistantName} Error] Failed to connect to WhatsApp:`, err?.message ?? err);
      console.log("\nYou may retry or inspect logs. Type 'exit' to quit.\n");
    }

    // Main interaction loop
    while (true) {
      try {
        const userInput = await this.#rl.question(`${this.#callerName} > `);
        const trimmed = userInput.trim();

        if (!trimmed) continue;

        if (trimmed.toLowerCase() === "exit" || trimmed.toLowerCase() === "quit") {
          console.log(`\n[${this.#assistantName}] Shutting down systems. Have a wonderful evening, Sir.`);
          break;
        }

        if (trimmed.toLowerCase() === "status") {
          this.#printStatus();
          continue;
        }

        if (trimmed.toLowerCase() === "history") {
          this.#printCallHistory();
          continue;
        }

        if (trimmed.toLowerCase() === "help") {
          this.#printHelp();
          continue;
        }

        console.log(`\n[${this.#assistantName}] Thinking...`);
        await this.#processUserMessage(trimmed);
        console.log("");
      } catch (err: any) {
        console.error(`\n[${this.#assistantName}] An unexpected error occurred:`, err?.message ?? err);
      }
    }

    this.#whatsapp?.disconnect();
    this.#rl.close();
    process.exit(0);
  }

  /**
   * Process user input via Gemini Agent Brain.
   */
  async #processUserMessage(userText: string): Promise<void> {
    const result = await this.#brain.processUserMessage(userText);
    console.log(`[${this.#assistantName}] ${result.response}`);
  }

  /**
   * Deterministic WhatsApp VoIP call executor with Gemini 3.8 Live bridge.
   */
  async #executeOutboundCall(cmd: {
    phoneNumber: string;
    targetName: string;
    objective: string;
    conversationMode?: string;
  }): Promise<Record<string, any>> {
    if (!this.#whatsapp || !this.#whatsapp.isConnected) {
      console.log(`[${this.#assistantName}] WhatsApp client is reconnecting...`);
      await this.#whatsapp?.connect();
    }

    const recipient = cmd.targetName || "the recipient";
    const phone = cmd.phoneNumber;
    const objective = cmd.objective;
    const mode = cmd.conversationMode || this.#config.conversationMode;

    console.log(`\n========================================================================`);
    console.log(` [${this.#assistantName}] INITIATING OUTBOUND WHATSAPP VOIP CALL`);
    console.log(`========================================================================`);
    console.log(` Target:       ${recipient} (+${phone})`);
    console.log(` Objective:    "${objective}"`);
    console.log(` Mode:         ${mode}`);
    console.log(` AI Voice:     ${this.#config.voiceDefaultName}`);
    console.log(` Language:     Hinglish (Natural conversational blend)`);
    console.log(` On Behalf Of: ${this.#callerName}`);
    console.log(`========================================================================\n`);

    let call: CallSession;
    try {
      call = await this.#whatsapp!.placeCall(phone, this.#config.callDurationMs);
      this.#activeCallSession = call;
    } catch (err: any) {
      this.#activeCallSession = null;
      console.error(`[${this.#assistantName} Error] Could not initiate call: ${err?.message ?? err}`);
      return {
        success: false,
        error: `Could not initiate call: ${err?.message ?? err}`,
      };
    }

    console.log(`[${this.#assistantName}] Call ID: ${call.callId}. Signaling remote device...`);

    call.on("ringing", () => {
      console.log(`[${this.#assistantName}] Remote phone is ringing... 🔔`);
    });

    // Wait for answer
    const answered = await new Promise<boolean>((resolve) => {
      const onConnected = () => {
        call.off("ended", onEnded);
        resolve(true);
      };
      const onEnded = () => {
        call.off("connected", onConnected);
        resolve(false);
      };
      call.once("connected", onConnected);
      call.once("ended", onEnded);
    });

    if (!answered) {
      this.#activeCallSession = null;
      const metrics = call.getMetrics();
      console.log(`\n[${this.#assistantName}] Call was not answered or ended early. Reason: ${metrics.endReason || "rejected/timeout"}.`);
      return {
        success: false,
        callId: call.callId,
        targetNumber: phone,
        durationSec: "0.0",
        outcome: metrics.endReason || "Not Answered",
        recipientReply: "No verbal reply detected.",
        summary: `Call to ${recipient} was not answered or rejected early.`,
      };
    }

    console.log(`\n[${this.#assistantName}] Call ANSWERED! Media flowing full-duplex.`);
    console.log(`[${this.#assistantName}] Connecting Gemini 3.8 Live session (Voice: ${this.#config.voiceDefaultName})...\n`);

    // Prepare system prompt for Gemini 3.8 Live
    const systemInstruction = buildSystemPrompt({
      callerName: this.#callerName,
      assistantName: this.#assistantName,
      recipientName: recipient,
      objective: objective,
      language: "hinglish",
      mode,
    });

    const gemini = new GeminiLiveSession({
      apiKey: this.#config.geminiApiKey,
      model: this.#config.geminiModel,
      voiceName: this.#config.voiceDefaultName,
      systemInstruction,
    });

    await gemini.connect();
    console.log(`[${this.#assistantName}] Gemini 3.8 Live active. Bridging raw PCM audio bidirectional...\n`);

    const bridge = new RealtimeAudioBridge(call, gemini, {
      logsDir: this.#config.logsDir,
      farewellGracePeriodMs: this.#config.farewellGracePeriodMs,
    });

    // Live terminal speech monitor
    gemini.on("outputTranscription", (t: string) => {
      const clean = t.trim();
      if (clean) {
        process.stdout.write(`\r[Live Voice - ${this.#assistantName} (${this.#config.voiceDefaultName})]: "${clean}"\n`);
      }
    });

    gemini.on("interimInputTranscription", (t: string) => {
      const clean = t.trim();
      if (clean) {
        process.stdout.write(`\r[Live Voice - ${recipient} (speaking...)]: "${clean}" `);
      }
    });

    gemini.on("inputTranscription", (t: string) => {
      const clean = t.trim();
      if (clean) {
        process.stdout.write(`\r[Live Voice - ${recipient}]: "${clean}"                 \n`);
      }
    });

    gemini.on("interrupted", () => {
      console.log(`\r[Barge-in] ${recipient} spoke while ${this.#assistantName} was speaking. Audio queue flushed.`);
    });

    bridge.start();

    // Trigger initial greeting in Hinglish
    gemini.sendTextMessage(
      `Call is connected to ${recipient}. Start immediately by greeting them warmly in natural Hinglish, introduce yourself as ${this.#callerName}'s AI assistant (${this.#assistantName}), and state the purpose of your call: ${objective}`,
      true
    );

    // Wait until call finishes
    const endReason = await call.waitForEnd();
    this.#activeCallSession = null;
    console.log(`\n[${this.#assistantName}] Call finished (reason: ${endReason}). Cleaning up media session...`);

    bridge.stop();
    gemini.close();

    const metrics = call.getMetrics();
    const transcript = bridge.getTranscript();

    console.log(`\n[${this.#assistantName}] Compiling grounded post-call debriefing for you, Sir...\n`);
    const report = await generateCallReport(
      this.#config.geminiApiKey,
      this.#callerName,
      recipient,
      phone,
      transcript,
      metrics,
      this.#config.geminiModel,
      this.#config.fallbackModel
    );

    // Save into Persistent Call History!
    const record: CallHistoryEntry = {
      callId: call.callId,
      targetName: recipient,
      phoneNumber: phone,
      objective,
      callOutcome: report.callOutcome,
      recipientReply: report.recipientReply,
      summary: report.summary,
      transcript,
      durationSec: (metrics.durationMs / 1000).toFixed(1),
      timestamp: Date.now(),
      conversationMode: mode,
      conditions: report.conditions,
      commitments: report.commitments,
      actionItems: report.actionItems,
      metrics: {
        durationMs: metrics.durationMs,
        inboundFramesCount: metrics.inboundFramesCount,
        outboundFramesPushed: metrics.outboundFramesPushed,
        interruptionDiscards: metrics.interruptionDiscards,
      },
    };

    await this.#historyManager.addRecord(record);

    // Structured debrief card
    console.log("========================================================================");
    console.log("                     POST-CALL DEBRIEF REPORT                           ");
    console.log("========================================================================");
    console.log(` Recipient:        ${recipient} (+${phone})`);
    console.log(` Duration:         ${record.durationSec} seconds`);
    console.log(` Call Outcome:     ${report.callOutcome}`);
    console.log(` Interruptions:    ${metrics.interruptionDiscards}`);
    console.log("------------------------------------------------------------------------");
    console.log(` WHAT ${recipient.toUpperCase()} REPLIED:`);
    console.log(` ${report.recipientReply}`);
    if (report.conditions.length > 0) {
      console.log(" CONDITIONS STATED:");
      report.conditions.forEach((c) => console.log(` - ${c}`));
    }
    console.log("------------------------------------------------------------------------");
    console.log(" EXECUTIVE SUMMARY:");
    console.log(report.summary);
    console.log("------------------------------------------------------------------------");
    if (transcript.length > 0) {
      console.log(" VERBATIM CONVERSATION LOG:");
      for (const entry of transcript) {
        const speaker = entry.role === "user" ? recipient : `${this.#assistantName} (${this.#config.voiceDefaultName})`;
        console.log(`   ${speaker}: "${entry.text}"`);
      }
    } else {
      console.log(" (No spoken words detected in audio stream)");
    }
    console.log("========================================================================");
    console.log(` Full transcript archived to: ${this.#config.logsDir}`);
    console.log("========================================================================\n");
    console.log(`[${this.#assistantName}] I am standing by for your next instruction, ${this.#callerName}.`);

    return {
      success: true,
      callId: call.callId,
      targetName: recipient,
      phoneNumber: phone,
      durationSec: record.durationSec,
      callOutcome: report.callOutcome,
      recipientReply: report.recipientReply,
      summary: report.summary,
      conditions: report.conditions,
      commitments: report.commitments,
    };
  }

  #printStatus(): void {
    const latest = this.#historyManager.getLatest();
    console.log("\n--- J.A.R.V.I.S. SYSTEM STATUS ---");
    console.log(`User:                 ${this.#callerName}`);
    console.log(`WhatsApp Connected:   ${this.#whatsapp?.isConnected ?? false}`);
    console.log(`Active Call:          ${this.#activeCallSession ? "YES (" + this.#activeCallSession.callId + ")" : "NO (IDLE)"}`);
    console.log(`Voice Preset:         ${this.#config.voiceDefaultName}`);
    console.log(`Primary Brain:        ${this.#config.geminiModel}`);
    console.log(`Call Brain:           ${this.#config.geminiModel}`);
    console.log(`Post-Call Brain:      ${this.#config.geminiModel}`);
    console.log(`Fallback Brain:       ${this.#config.fallbackModel}`);
    console.log(`Session Calls:        ${this.#historyManager.count}`);
    if (latest) {
      console.log(`Last Call Target:     ${latest.targetName} (+${latest.phoneNumber})`);
      console.log(`Last Call Outcome:    ${latest.callOutcome}`);
      console.log(`Last Call Reply:      "${latest.recipientReply}"`);
    }
    console.log("---------------------------------\n");
  }

  #printCallHistory(): void {
    const calls = this.#historyManager.getAll();
    console.log("\n--- CALL HISTORY ---");
    if (calls.length === 0) {
      console.log("No calls in history.");
    } else {
      calls.forEach((c, idx) => {
        const time = new Date(c.timestamp).toLocaleTimeString();
        console.log(`[#${idx + 1}] [${time}] Target: ${c.targetName} (+${c.phoneNumber})`);
        console.log(`     Objective: "${c.objective}"`);
        console.log(`     Outcome:   ${c.callOutcome}`);
        console.log(`     Reply:     "${c.recipientReply}"\n`);
      });
    }
    console.log("--------------------\n");
  }

  #printHelp(): void {
    console.log("\n--- J.A.R.V.I.S. INTERACTION GUIDE ---");
    console.log("1. Ask questions from memory naturally:");
    console.log('   "usne kya bola?"');
    console.log('   "inform to kro ki uska response kya tha?"');
    console.log('   "what was the condition?"');
    console.log("2. Place calls or follow-ups naturally:");
    console.log('   "call John at 9876543210 and ask if he is joining the conference"');
    console.log(`   "call him again and tell him ${this.#callerName} agreed"`);
    console.log('   "same number pe call karo"');
    console.log("3. System commands:");
    console.log('   "status"   - Check system & active call state');
    console.log('   "history"  - View complete call history');
    console.log('   "exit"     - Conclude session cleanly\n');
  }
}

// Self-launch if invoked directly
if (process.argv[1]?.includes("src/chat/session") || process.argv[1]?.includes("src\\chat\\session")) {
  const session = new JarvisChatSession();
  session.start();
}
