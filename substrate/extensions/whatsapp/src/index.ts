/**
 * Main application entrypoint and objective-based calling coordinator.
 *
 * Standalone system:
 * 1. Resolves target contact / phone number.
 * 2. Connects WhatsApp client (with interactive QR authentication when needed).
 * 3. Initiates 1:1 outbound WhatsApp voice call in realtime audio mode.
 * 4. Waits for connected state.
 * 5. Establishes Gemini 3.8 Live bidirectional session.
 * 6. Bridges realtime full-duplex audio with barge-in interruption.
 * 7. Collects transcripts, logs summary, and closes cleanly on completion.
 */

import { exec } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { loadConfig } from "./config.js";
import { WhatsAppManager } from "./engine/client.js";
import { ContactResolver } from "./contacts/resolver.js";
import { GeminiLiveSession } from "./gemini/live.js";
import { buildSystemPrompt } from "./gemini/prompts.js";
import { RealtimeAudioBridge } from "./bridge/realtime-audio.js";
import { installSecuritySanitizer } from "./security/sanitizer.js";
import { generateCallReport, type CallReportResult } from "./chat/reporter.js";
import { CallHistoryManager } from "./memory/call-history.js";

// Shield console and streams against cryptographic leaks
installSecuritySanitizer();

export interface CallWhatsAppOptions {
  target: string;
  objective: string;
  durationMs?: number;
  mode?: string;
}

export interface CallWhatsAppResult {
  success: boolean;
  callId: string;
  targetNumber: string;
  durationMs: number;
  transcriptPath?: string;
  summaryPath?: string;
  summary?: string;
  recipientReply?: string;
  error?: string;
}

/**
 * High-level callable function: callWhatsApp({ target, objective, mode, durationMs })
 */
export async function callWhatsApp(options: CallWhatsAppOptions): Promise<CallWhatsAppResult> {
  const config = loadConfig();
  if (!config.geminiApiKey) {
    throw new Error("GEMINI_API_KEY is required for autonomous WhatsApp voice calls.");
  }
  const resolver = new ContactResolver({
    defaultCountryCode: config.defaultCountryCode,
    dbPath: config.whatsappDbPath,
    authDir: config.whatsappAuthDir,
    contactsFilePath: config.contactsFilePath,
  });
  const contact = resolver.resolveTarget(options.target);

  console.log(`[Call Manager] Preparing outbound call to ${contact.name ?? "direct number"} (${contact.formatted})...`);
  console.log(`[Call Manager] Objective: "${options.objective}"`);

  const whatsapp = new WhatsAppManager({
    authDir: config.whatsappAuthDir,
    dbPath: config.whatsappDbPath,
  });

  console.log("[Call Manager] Connecting WhatsApp client...");
  await whatsapp.connect();
  console.log("[Call Manager] WhatsApp client connected and ready.");

  const durationMs = options.durationMs || config.callDurationMs;
  console.log(`[Call Manager] Placing 1:1 voice call to ${contact.phoneNumber}...`);
  const call = await whatsapp.placeCall(contact.phoneNumber, durationMs);

  console.log(`[Call Manager] Call initiated (ID: ${call.callId}). Waiting for ringing and answer...`);

  call.on("ringing", () => {
    console.log("[Call Event] Remote phone is ringing...");
  });

  // Wait for call to be answered and connected
  const connected = await new Promise<boolean>((resolveConnected) => {
    const onConnected = () => {
      call.off("ended", onEnded);
      resolveConnected(true);
    };
    const onEnded = () => {
      call.off("connected", onConnected);
      resolveConnected(false);
    };
    call.once("connected", onConnected);
    call.once("ended", onEnded);
  });

  if (!connected) {
    console.error("[Call Manager] Call ended or was rejected before connecting.");
    const metrics = call.getMetrics();
    whatsapp.disconnect();
    return {
      success: false,
      callId: call.callId,
      targetNumber: contact.phoneNumber,
      durationMs: 0,
      error: metrics.endReason || "Call ended before connection.",
    };
  }

  console.log("[Call Manager] Call ANSWERED and CONNECTED! Media channels open.");

  // Build Gemini 3.8 Live session with objective prompt
  const systemInstruction = buildSystemPrompt({
    callerName: config.userName,
    assistantName: config.assistantName,
    recipientName: contact.name,
    objective: options.objective,
    mode: options.mode || config.conversationMode,
  });

  const gemini = new GeminiLiveSession({
    apiKey: config.geminiApiKey,
    model: config.geminiModel,
    voiceName: config.voiceDefaultName,
    systemInstruction,
  });

  console.log(`[Call Manager] Establishing Gemini 3.8 Live session (${config.geminiModel})...`);
  await gemini.connect();
  console.log("[Call Manager] Gemini Live session connected.");

  // Start the full-duplex realtime audio bridge
  const bridge = new RealtimeAudioBridge(call, gemini, {
    logsDir: config.logsDir,
    farewellGracePeriodMs: config.farewellGracePeriodMs,
  });
  bridge.start();
  console.log("[Call Manager] Full-duplex realtime audio bridge active.");

  // Send an initial silent prompt or turn trigger so Gemini starts the conversation
  gemini.sendTextMessage(
    `The call has just connected to ${contact.name ?? "the recipient"}. Begin by greeting them warmly and stating the purpose of your call according to your objective.`,
    true
  );

  // Await conclusion of call
  const endReason = await call.waitForEnd();
  console.log(`[Call Manager] Call finished with reason: ${endReason}`);

  // Stop bridge and save records
  bridge.stop();
  gemini.close();
  whatsapp.disconnect();

  const metrics = call.getMetrics();
  const transcript = bridge.getTranscript();

  // Generate grounded after-call debrief and summary
  let report: CallReportResult | undefined;
  if (config.geminiApiKey && transcript.length > 0) {
    try {
      report = await generateCallReport(
        config.geminiApiKey,
        config.userName,
        contact.name || "the recipient",
        contact.phoneNumber,
        transcript,
        metrics,
        config.geminiModel,
        config.fallbackModel
      );
    } catch (reportErr) {
      console.error("[Call Manager] Error generating AI call report:", reportErr);
    }
  }

  // Derive final summary and recipient reply
  const userUtterances = transcript
    .filter((t) => t.role === "user")
    .map((t) => t.text.trim())
    .filter(Boolean);
  const fallbackUserReply =
    userUtterances.length > 0 ? userUtterances.join("; ") : "No verbal reply detected in call.";
  const fallbackSummary = `Call to ${contact.formatted} concluded after ${(metrics.durationMs / 1000).toFixed(1)}s. Recipient replied: "${fallbackUserReply}"`;

  const finalRecipientReply = report?.recipientReply || fallbackUserReply;
  const finalSummary = report?.summary || fallbackSummary;
  const finalOutcome =
    report?.callOutcome || (userUtterances.length > 0 ? "Completed with Response" : "Completed without Dialogue");

  // Save transcript with embedded summary and reply
  const transcriptFiles = bridge.saveTranscript({
    summary: finalSummary,
    recipientReply: finalRecipientReply,
    report,
  });

  // Record into persistent call history
  try {
    const history = new CallHistoryManager(config.callHistoryFile);
    await history.addRecord({
      callId: call.callId,
      targetName: contact.name || contact.phoneNumber,
      phoneNumber: contact.phoneNumber,
      objective: options.objective,
      callOutcome: finalOutcome,
      recipientReply: finalRecipientReply,
      summary: finalSummary,
      transcript,
      durationSec: (metrics.durationMs / 1000).toFixed(1),
      timestamp: Date.now(),
      conversationMode: options.mode || config.conversationMode,
      conditions: report?.conditions || [],
      commitments: report?.commitments || [],
      actionItems: report?.actionItems || [],
      metrics: {
        durationMs: metrics.durationMs,
        inboundFramesCount: metrics.inboundFramesCount,
        outboundFramesPushed: metrics.outboundFramesPushed,
        interruptionDiscards: metrics.interruptionDiscards,
        bargeInCount: bridge.getMetrics().bargeInCount,
        userTurnCount: bridge.getMetrics().userTurnCount,
      },
    });

    // Also persist into extension-local logs/call-history.json if distinct from config.callHistoryFile
    const localLogsHistory = resolve(process.cwd(), "logs", "call-history.json");
    if (resolve(config.callHistoryFile) !== localLogsHistory) {
      try {
        const localHistory = new CallHistoryManager(localLogsHistory);
        await localHistory.addRecord({
          callId: call.callId,
          targetName: contact.name || contact.phoneNumber,
          phoneNumber: contact.phoneNumber,
          objective: options.objective,
          callOutcome: finalOutcome,
          recipientReply: finalRecipientReply,
          summary: finalSummary,
          transcript,
          durationSec: (metrics.durationMs / 1000).toFixed(1),
          timestamp: Date.now(),
          conversationMode: options.mode || config.conversationMode,
          conditions: report?.conditions || [],
          commitments: report?.commitments || [],
          actionItems: report?.actionItems || [],
        });
      } catch {}
    }
  } catch (histErr) {
    console.error("[Call Manager] Error saving call history:", histErr);
  }

  return {
    success: true,
    callId: call.callId,
    targetNumber: contact.phoneNumber,
    durationMs: metrics.durationMs,
    transcriptPath: transcriptFiles.textPath,
    summaryPath: transcriptFiles.jsonPath,
    summary: finalSummary,
    recipientReply: finalRecipientReply,
  };
}

/**
 * Interactive WhatsApp login handler: generates QR code, renders HTML viewer, and waits for linking.
 */
async function handleLogin(options: { json: boolean; forceRefresh?: boolean }): Promise<void> {
  const config = loadConfig();
  const manager = new WhatsAppManager({
    authDir: config.whatsappAuthDir,
    dbPath: config.whatsappDbPath,
  });

  if (manager.hasPersistedAuth && !options.forceRefresh) {
    const res = {
      success: true,
      authenticated: true,
      message: "WhatsApp is already connected and authenticated.",
    };
    if (options.json) {
      console.log(`[CALL_RESULT_JSON]${JSON.stringify(res)}[/CALL_RESULT_JSON]`);
    } else {
      console.log(res.message);
    }
    process.exit(0);
  }

  if (options.forceRefresh) {
    manager.logout();
  }

  console.log("[WhatsApp Auth] Generating QR code for WhatsApp Web authentication...");

  const qrManager = new WhatsAppManager({
    authDir: config.whatsappAuthDir,
    dbPath: config.whatsappDbPath,
    onQrCode: (qr: string) => {
      const htmlDir = resolve(config.workspaceDir || process.cwd(), "data", "whatsapp");
      mkdirSync(htmlDir, { recursive: true });
      const htmlPath = resolve(htmlDir, "login_qr.html");
      const htmlContent = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>JARVIS - WhatsApp Authentication</title>
  <style>
    body { background: #0b0f19; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
    .card { background: #131b2e; border: 1px solid #1e293b; border-radius: 16px; padding: 32px; max-width: 480px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
    h1 { color: #38bdf8; margin-top: 0; font-size: 24px; }
    p { color: #94a3b8; font-size: 15px; line-height: 1.6; }
    .qr-box { background: white; padding: 16px; border-radius: 12px; display: inline-block; margin: 20px 0; }
    .steps { text-align: left; background: #0f172a; padding: 16px; border-radius: 8px; margin-top: 16px; }
    .steps li { margin-bottom: 8px; font-size: 14px; color: #cbd5e1; }
    .status { color: #34d399; font-weight: 600; margin-top: 16px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>JARVIS - WhatsApp Link</h1>
    <p>Scan this QR code using WhatsApp on your mobile phone to link your account to JARVIS.</p>
    <div class="qr-box">
      <img src="https://api.qrserver.com/v1/create-qr-code/?size=320x320&data=${encodeURIComponent(qr)}"
           onerror="this.onerror=null; this.src='https://chart.googleapis.com/chart?chs=320x320&cht=qr&chl=${encodeURIComponent(qr)}';"
           width="320" height="320" alt="WhatsApp QR Code" />
    </div>
    <div class="steps">
      <ol>
        <li>Open <strong>WhatsApp</strong> on your phone.</li>
        <li>Tap <strong>Settings</strong> (iOS) or <strong>Three Dots</strong> (Android).</li>
        <li>Select <strong>Linked Devices</strong> &rarr; <strong>Link a Device</strong>.</li>
        <li>Point your phone camera at this QR code.</li>
      </ol>
    </div>
    <div class="status">Waiting for scan...</div>
  </div>
</body>
</html>`;
      writeFileSync(htmlPath, htmlContent, "utf-8");

      // Also write copy in current working directory data/whatsapp if different
      const rootHtmlPath = resolve(process.cwd(), "data", "whatsapp", "login_qr.html");
      if (rootHtmlPath !== htmlPath) {
        try {
          mkdirSync(resolve(process.cwd(), "data", "whatsapp"), { recursive: true });
          writeFileSync(rootHtmlPath, htmlContent, "utf-8");
        } catch {}
      }

      // Open in default browser on Windows
      if (process.platform === "win32") {
        try {
          exec(`cmd /c start "" "${htmlPath}"`);
        } catch {}
      }

      console.log(`[WHATSAPP_QR_JSON]${JSON.stringify({ qr, htmlPath })}[/WHATSAPP_QR_JSON]`);
      console.log(`[WhatsApp Auth] QR code saved to ${htmlPath}`);
      console.log("Please scan the QR code in your opened browser window or in the console via WhatsApp > Linked Devices > Link a Device.\n");
    },
  });

  try {
    await qrManager.connect();
    console.log("[WhatsApp Auth] WhatsApp device connected and authenticated successfully!");
    const res = {
      success: true,
      authenticated: true,
      message: "WhatsApp authentication successful. Device linked.",
    };
    if (options.json) {
      console.log(`[CALL_RESULT_JSON]${JSON.stringify(res)}[/CALL_RESULT_JSON]`);
    }
    qrManager.disconnect();
    process.exit(0);
  } catch (err: any) {
    const errorMsg = err?.message ?? String(err);
    if (options.json) {
      console.log(`[CALL_RESULT_JSON]${JSON.stringify({ success: false, error: errorMsg })}[/CALL_RESULT_JSON]`);
    } else {
      console.error("[WhatsApp Auth Failed]", errorMsg);
    }
    process.exit(1);
  }
}

/**
 * Silent network connection warmup without displaying console banners.
 */
async function handleWarmup(): Promise<void> {
  const config = loadConfig();
  const manager = new WhatsAppManager({
    authDir: config.whatsappAuthDir,
    dbPath: config.whatsappDbPath,
    silent: true,
  });

  if (!manager.hasPersistedAuth) {
    process.exit(0);
  }

  try {
    await manager.connect();
    // Keep connection alive in background
    await new Promise<void>(() => {});
  } catch {
    process.exit(0);
  }
}

/**
 * Purge persistent authentication credentials.
 */
function handleLogout(options: { json: boolean }): void {
  const config = loadConfig();
  const manager = new WhatsAppManager({
    authDir: config.whatsappAuthDir,
    dbPath: config.whatsappDbPath,
  });
  manager.logout();
  const res = {
    success: true,
    message: "WhatsApp session logged out and persistent credentials cleared.",
  };
  if (options.json) {
    console.log(`[CALL_RESULT_JSON]${JSON.stringify(res)}[/CALL_RESULT_JSON]`);
  } else {
    console.log(res.message);
  }
  process.exit(0);
}

// ─────────────────────────────────────────────────────────────────────────────
// CLI Handler
// ─────────────────────────────────────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);
  let action = "call";
  let target = "";
  let objective = "";
  let mode = "";
  let durationMs: number | undefined;
  let jsonOutput = false;
  let forceRefresh = false;
  let message = "";
  let filePath = "";
  let caption = "";
  let fileAs = "";
  let replyTo = "";
  let reactionEmoji = "";
  let phone = "";
  let chatJid = "";
  let msgId = "";
  let settleMs: number | undefined;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--action" && args[i + 1]) {
      action = args[++i];
    } else if ((arg === "--id" || arg === "--msg-id") && args[i + 1]) {
      msgId = args[++i];
    } else if (arg === "--login") {
      action = "login";
    } else if (arg === "--logout") {
      action = "logout";
    } else if (arg === "--status") {
      action = "status";
    } else if (arg === "--diagnostics" || arg === "-d") {
      action = "diagnostics";
    } else if (arg === "--sync") {
      action = "sync";
    } else if (arg === "--warmup") {
      action = "warmup";
    } else if (arg === "--force") {
      forceRefresh = true;
    } else if (arg === "--target" && args[i + 1]) {
      target = args[++i];
    } else if (arg === "--to" && args[i + 1]) {
      target = args[++i];
    } else if (arg === "--objective" && args[i + 1]) {
      objective = args[++i];
    } else if (arg === "--mode" && args[i + 1]) {
      mode = args[++i];
    } else if (arg === "--duration" && args[i + 1]) {
      durationMs = Number(args[++i]);
    } else if (arg === "--message" && args[i + 1]) {
      message = args[++i];
    } else if (arg === "--file" && args[i + 1]) {
      filePath = args[++i];
    } else if (arg === "--caption" && args[i + 1]) {
      caption = args[++i];
    } else if (arg === "--as" && args[i + 1]) {
      fileAs = args[++i];
    } else if (arg === "--reply-to" && args[i + 1]) {
      replyTo = args[++i];
    } else if (arg === "--reaction" && args[i + 1]) {
      reactionEmoji = args[++i];
    } else if (arg === "--emoji" && args[i + 1]) {
      reactionEmoji = args[++i];
    } else if (arg === "--phone" && args[i + 1]) {
      phone = args[++i];
    } else if (arg === "--chat" && args[i + 1]) {
      chatJid = args[++i];
    } else if (arg === "--settle" && args[i + 1]) {
      settleMs = Number(args[++i]);
    } else if (arg === "--json") {
      jsonOutput = true;
    }
  }

  if (action === "login") {
    await handleLogin({ json: jsonOutput, forceRefresh });
    return;
  }

  if (action === "logout") {
    handleLogout({ json: jsonOutput });
    return;
  }

  if (action === "warmup") {
    await handleWarmup();
    return;
  }

  if (action === "status" || action === "diagnostics") {
    const config = loadConfig();
    const manager = new WhatsAppManager({
      authDir: config.whatsappAuthDir,
      dbPath: config.whatsappDbPath,
    });
    const diagnostics = manager.getDiagnostics();
    if (jsonOutput) {
      console.log(`[CALL_RESULT_JSON]${JSON.stringify({ success: true, ...diagnostics })}[/CALL_RESULT_JSON]`);
    } else {
      console.log(JSON.stringify(diagnostics, null, 2));
    }
    process.exit(0);
  }

  if (action === "sync") {
    try {
      const config = loadConfig();
      const manager = new WhatsAppManager({
        authDir: config.whatsappAuthDir,
        dbPath: config.whatsappDbPath,
      });
      const diagnostics = await manager.sync(settleMs || 5000);
      manager.disconnect();
      const out = { success: true, ...diagnostics };
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(out)}[/CALL_RESULT_JSON]`);
      } else {
        console.log(JSON.stringify(out, null, 2));
      }
      process.exit(0);
    } catch (err: any) {
      const out = { success: false, error: err?.message || String(err) };
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(out)}[/CALL_RESULT_JSON]`);
      } else {
        console.error(err);
      }
      process.exit(1);
    }
  }

  if (action === "send-text") {
    try {
      const config = loadConfig();
      const manager = new WhatsAppManager({
        authDir: config.whatsappAuthDir,
        dbPath: config.whatsappDbPath,
      });
      const recipient = target || phone;
      if (!recipient) throw new Error("Recipient target (--to) is required.");
      if (!message) throw new Error("Message text (--message) is required.");
      const result = await manager.sendText(recipient, message, replyTo || undefined);
      manager.disconnect();
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(result)}[/CALL_RESULT_JSON]`);
      } else {
        console.log(JSON.stringify(result, null, 2));
      }
      process.exit(result.success ? 0 : 1);
    } catch (err: any) {
      const out = { success: false, sent: false, error: err?.message || String(err) };
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(out)}[/CALL_RESULT_JSON]`);
      } else {
        console.error(err);
      }
      process.exit(1);
    }
  }

  if (action === "send-file") {
    try {
      const config = loadConfig();
      const manager = new WhatsAppManager({
        authDir: config.whatsappAuthDir,
        dbPath: config.whatsappDbPath,
      });
      const recipient = target || phone;
      if (!recipient) throw new Error("Recipient target (--to) is required.");
      if (!filePath) throw new Error("File path (--file) is required.");
      const result = await manager.sendFile(recipient, filePath, caption || undefined, fileAs || undefined);
      manager.disconnect();
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(result)}[/CALL_RESULT_JSON]`);
      } else {
        console.log(JSON.stringify(result, null, 2));
      }
      process.exit(result.success ? 0 : 1);
    } catch (err: any) {
      const out = { success: false, sent: false, error: err?.message || String(err) };
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(out)}[/CALL_RESULT_JSON]`);
      } else {
        console.error(err);
      }
      process.exit(1);
    }
  }

  if (action === "send-voice") {
    try {
      const config = loadConfig();
      const manager = new WhatsAppManager({
        authDir: config.whatsappAuthDir,
        dbPath: config.whatsappDbPath,
      });
      const recipient = target || phone;
      if (!recipient) throw new Error("Recipient target (--to) is required.");
      if (!filePath) throw new Error("File path (--file) is required.");
      const result = await manager.sendVoice(recipient, filePath);
      manager.disconnect();
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(result)}[/CALL_RESULT_JSON]`);
      } else {
        console.log(JSON.stringify(result, null, 2));
      }
      process.exit(result.success ? 0 : 1);
    } catch (err: any) {
      const out = { success: false, sent: false, error: err?.message || String(err) };
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(out)}[/CALL_RESULT_JSON]`);
      } else {
        console.error(err);
      }
      process.exit(1);
    }
  }

  if (action === "send-reaction") {
    try {
      const config = loadConfig();
      const manager = new WhatsAppManager({
        authDir: config.whatsappAuthDir,
        dbPath: config.whatsappDbPath,
      });
      const recipient = target || phone;
      if (!recipient) throw new Error("Recipient target (--to) is required.");
      if (!reactionEmoji) throw new Error("Reaction emoji (--reaction/--emoji) is required.");
      const targetReactionMsgId = msgId || replyTo;
      if (!targetReactionMsgId) throw new Error("Message ID (--id or --reply-to) is required.");
      const result = await manager.sendReaction(recipient, targetReactionMsgId, reactionEmoji);
      manager.disconnect();
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(result)}[/CALL_RESULT_JSON]`);
      } else {
        console.log(JSON.stringify(result, null, 2));
      }
      process.exit(result.success ? 0 : 1);
    } catch (err: any) {
      const out = { success: false, sent: false, error: err?.message || String(err) };
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(out)}[/CALL_RESULT_JSON]`);
      } else {
        console.error(err);
      }
      process.exit(1);
    }
  }

  if (action === "check-number") {
    try {
      const config = loadConfig();
      const manager = new WhatsAppManager({
        authDir: config.whatsappAuthDir,
        dbPath: config.whatsappDbPath,
      });
      const checkTarget = phone || target;
      if (!checkTarget) throw new Error("Phone number (--phone) is required.");
      const result = await manager.checkNumber(checkTarget);
      manager.disconnect();
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(result)}[/CALL_RESULT_JSON]`);
      } else {
        console.log(JSON.stringify(result, null, 2));
      }
      process.exit(0);
    } catch (err: any) {
      const out = { phone: phone || target, exists: false, error: err?.message || String(err) };
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(out)}[/CALL_RESULT_JSON]`);
      } else {
        console.error(err);
      }
      process.exit(1);
    }
  }

  if (action === "mark-read") {
    try {
      const config = loadConfig();
      const manager = new WhatsAppManager({
        authDir: config.whatsappAuthDir,
        dbPath: config.whatsappDbPath,
      });
      const targetChat = chatJid || target || phone;
      if (!targetChat) throw new Error("Chat JID (--chat) is required.");
      const result = await manager.markRead(targetChat);
      manager.disconnect();
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(result)}[/CALL_RESULT_JSON]`);
      } else {
        console.log(JSON.stringify(result, null, 2));
      }
      process.exit(0);
    } catch (err: any) {
      const out = { success: false, error: err?.message || String(err) };
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(out)}[/CALL_RESULT_JSON]`);
      } else {
        console.error(err);
      }
      process.exit(1);
    }
  }

  if (action === "download-media") {
    try {
      const config = loadConfig();
      const manager = new WhatsAppManager({
        authDir: config.whatsappAuthDir,
        dbPath: config.whatsappDbPath,
      });
      const targetChat = chatJid || target;
      const targetMsgId = msgId;
      if (!targetChat || !targetMsgId) {
        throw new Error("Both --chat and --id are required for download-media.");
      }
      const result = await manager.downloadMedia(targetChat, targetMsgId, filePath || undefined);
      manager.disconnect();
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(result)}[/CALL_RESULT_JSON]`);
      } else {
        console.log(JSON.stringify(result, null, 2));
      }
      process.exit(result.success ? 0 : 1);
    } catch (err: any) {
      const out = { success: false, error: err?.message || String(err) };
      if (jsonOutput) {
        console.log(`[CALL_RESULT_JSON]${JSON.stringify(out)}[/CALL_RESULT_JSON]`);
      } else {
        console.error(err);
      }
      process.exit(1);
    }
  }

  // Fallback: place autonomous VoIP call
  if (!target) {
    const config = loadConfig();
    target = config.testWhatsAppNumber || "";
  }

  if (!target || !objective) {
    console.log(`
Usage:
  npm run call -- --target <number_or_name> --objective "<task_description>" [--mode <mode>] [--duration <ms>] [--json]
  npm run call -- --action login [--force] [--json]
  npm run call -- --action logout [--json]
  npm run call -- --action status [--json]
  npm run call -- --action sync [--settle <ms>] [--json]
  npm run call -- --action send-text --to <target> --message "<text>" [--reply-to <id>] [--json]
  npm run call -- --action send-file --to <target> --file <path> [--caption "<text>"] [--json]
  npm run call -- --action send-voice --to <target> --file <path> [--json]
  npm run call -- --action send-reaction --to <target> --id <msg_id> --reaction "<emoji>" [--json]
  npm run call -- --action check-number --phone <number> [--json]
  npm run call -- --action mark-read --chat <jid> [--json]
    `);
    process.exit(1);
  }

  try {
    const result = await callWhatsApp({ target, objective, mode, durationMs });
    if (jsonOutput) {
      console.log(`[CALL_RESULT_JSON]${JSON.stringify(result)}[/CALL_RESULT_JSON]`);
    } else {
      console.log("\n--- Final Call Result ---");
      console.log(JSON.stringify(result, null, 2));
    }
    process.exit(result.success ? 0 : 1);
  } catch (err: any) {
    const errorMsg = err?.message ?? String(err);
    if (jsonOutput) {
      console.log(`[CALL_RESULT_JSON]${JSON.stringify({ success: false, error: errorMsg })}[/CALL_RESULT_JSON]`);
    } else {
      console.error("\n[Fatal Error in Call]", errorMsg);
    }
    process.exit(1);
  }
}

if (process.argv[1]?.includes("index")) {
  main();
}
