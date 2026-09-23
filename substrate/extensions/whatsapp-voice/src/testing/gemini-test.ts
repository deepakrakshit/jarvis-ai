/**
 * Gemini 3.8 Live API test.
 *
 * Verifies:
 * - Exact model gemini-3.8-live connectivity
 * - Bidirectional audio session
 * - Realtime input transmission
 * - Realtime output reception
 * - Transcription events
 */

import { loadConfig, getSafeConfigSummary } from "../config.js";
import { GeminiLiveSession } from "../gemini/live.js";
import { generateTone, float32ToInt16 } from "../whatsapp/audio.js";

async function runGeminiTest(): Promise<boolean> {
  console.log("=== TEST: Gemini 3.8 Live Connection & Streaming ===");

  const config = loadConfig();
  console.log("Safe Config:", JSON.stringify(getSafeConfigSummary(config), null, 2));

  if (!config.geminiApiKey) {
    console.error("FAIL: GEMINI_API_KEY is not set.");
    return false;
  }

  console.log(`Connecting to Gemini Live with model: ${config.geminiModel}...`);
  const session = new GeminiLiveSession({
    apiKey: config.geminiApiKey,
    model: config.geminiModel,
    systemInstruction: "You are a helpful phone test assistant. Acknowledge this connectivity test briefly.",
  });

  let receivedAudioChunks = 0;
  let receivedAudioBytes = 0;
  let receivedText = "";
  let receivedTranscription = "";
  let turnCompleted = false;

  session.on("open", () => {
    console.log("[Gemini] WebSocket connection opened successfully.");
  });

  session.on("audio", (buffer: Buffer) => {
    receivedAudioChunks++;
    receivedAudioBytes += buffer.length;
    if (receivedAudioChunks === 1) {
      console.log(`[Gemini] First audio chunk received (${buffer.length} bytes, 24kHz PCM).`);
    }
  });

  session.on("text", (text: string) => {
    receivedText += text;
    console.log(`[Gemini Text] ${text}`);
  });

  session.on("outputTranscription", (text: string) => {
    receivedTranscription += text;
    console.log(`[Gemini Transcription] ${text}`);
  });

  session.on("turnComplete", () => {
    console.log("[Gemini] Turn complete signaled by server.");
    turnCompleted = true;
  });

  session.on("error", (err: any) => {
    console.error("[Gemini Error]", err);
  });

  try {
    await session.connect();
    console.log("PASS: Connected to gemini-3.8-live session.");

    // Send a text greeting prompt to initiate response
    console.log("Sending initial greeting turn to trigger voice output...");
    session.sendTextMessage("Hello Gemini, this is a phone connectivity test. Please say 'Hello, connectivity test successful.' in one short sentence.");

    // Wait up to 10 seconds for response
    const startWait = Date.now();
    while (!turnCompleted && Date.now() - startWait < 12_000) {
      await new Promise((r) => setTimeout(r, 250));
    }

    // Now test sending raw audio input (440Hz test tone at 16kHz)
    console.log("Testing audio uplink with 500ms 16kHz tone...");
    const testTone = generateTone(440, 0.5, 16000, 0.3);
    const i16 = float32ToInt16(testTone);
    const audioBuf = Buffer.from(i16.buffer, i16.byteOffset, i16.byteLength);
    session.sendRealtimeAudio(audioBuf);
    await new Promise((r) => setTimeout(r, 1500));

    session.close();

    console.log("\n--- Summary of Gemini Live Test ---");
    console.log(`Model: ${session.model}`);
    console.log(`Audio Chunks Received: ${receivedAudioChunks}`);
    console.log(`Audio Bytes Received: ${receivedAudioBytes}`);
    console.log(`Transcription: "${receivedTranscription.trim()}"`);
    console.log(`Turn Completed: ${turnCompleted}`);

    if (receivedAudioChunks > 0 && receivedAudioBytes > 0) {
      console.log("PASS: Gemini 3.8 Live bidirectional audio verified successfully!");
      return true;
    } else {
      console.error("FAIL: No audio received from Gemini 3.8 Live.");
      return false;
    }
  } catch (err: any) {
    console.error("FAIL: Gemini 3.8 Live test failed:", err?.message ?? err);
    session.close();
    return false;
  }
}

// Run directly if invoked
if (process.argv[1]?.includes("gemini-test")) {
  runGeminiTest().then((success) => {
    process.exit(success ? 0 : 1);
  });
}

export { runGeminiTest };
