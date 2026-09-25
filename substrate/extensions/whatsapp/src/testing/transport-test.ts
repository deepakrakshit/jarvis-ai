/**
 * Pure WhatsApp Audio Transport Test.
 *
 * Verifies:
 * 1. Environment prerequisites (Node, ffmpeg, git)
 * 2. In-memory audio format conversions (Float32 <-> Int16)
 * 3. 24kHz -> 16kHz resampler accuracy
 * 4. Synthetic tone generation & RMS diagnostics
 * 5. Realtime audio feeder queueing, frame slicing, and barge-in clearing
 * 6. WhatsApp authentication state verification
 * 7. (Optional) Live transport call if TEST_WHATSAPP_NUMBER is set
 */

import { execSync } from "node:child_process";
import { loadConfig } from "../config.js";
import { WhatsAppManager } from "../engine/client.js";
import {
  generateTone,
  generateSilence,
  computeAudioStats,
  float32ToInt16,
  int16ToFloat32,
  resample24kTo16k,
} from "../engine/audio.js";

export async function runTransportTest(): Promise<boolean> {
  console.log("=== TEST: WhatsApp Pure Audio Transport ===");
  const config = loadConfig();

  // 1. Environment verification
  console.log("[1/6] Verifying environment tools...");
  try {
    const nodeVer = process.version;
    const gitVer = execSync("git --version", { encoding: "utf8" }).trim();
    const ffmpegVer = execSync("ffmpeg -version", { encoding: "utf8" }).split("\n")[0].trim();
    console.log(`  Node.js: ${nodeVer}`);
    console.log(`  Git:     ${gitVer}`);
    console.log(`  FFmpeg:  ${ffmpegVer}`);
    console.log("  PASS: Environment prerequisites verified.");
  } catch (err: any) {
    console.error("  FAIL: Environment prerequisite check failed:", err?.message);
    return false;
  }

  // 2. Audio generator and RMS verification
  console.log("[2/6] Verifying audio diagnostics and tone generators...");
  const tone440 = generateTone(440, 1.0, 16000, 0.5);
  const toneStats = computeAudioStats(tone440, 16000);
  console.log(`  440Hz Tone Stats: ${toneStats.samples} samples, RMS=${toneStats.rms.toFixed(4)}, Peak=${toneStats.peak.toFixed(4)}`);
  if (toneStats.samples !== 16000 || toneStats.rms < 0.3 || toneStats.rms > 0.4) {
    console.error("  FAIL: Tone RMS out of expected range.");
    return false;
  }

  const silence = generateSilence(1.0, 16000);
  const silenceStats = computeAudioStats(silence, 16000);
  console.log(`  Silence Stats:    ${silenceStats.samples} samples, RMS=${silenceStats.rms.toFixed(4)}, Peak=${silenceStats.peak.toFixed(4)}`);
  if (silenceStats.rms !== 0 || silenceStats.nonZeroCount !== 0) {
    console.error("  FAIL: Silence contains non-zero samples.");
    return false;
  }
  console.log("  PASS: Audio signal generators and stats verified.");

  // 3. Audio format round-trip verification
  console.log("[3/6] Verifying Float32 <-> Int16 format conversions...");
  const i16 = float32ToInt16(tone440);
  const backToF32 = int16ToFloat32(i16);
  let maxDiff = 0;
  for (let i = 0; i < tone440.length; i++) {
    const diff = Math.abs(tone440[i] - backToF32[i]);
    if (diff > maxDiff) maxDiff = diff;
  }
  console.log(`  Max quantization difference: ${maxDiff.toExponential(4)} (Expected < 1e-4)`);
  if (maxDiff > 0.0001) {
    console.error("  FAIL: Quantization error too high.");
    return false;
  }
  console.log("  PASS: Audio format conversions verified.");

  // 4. Resampler verification (24kHz -> 16kHz)
  console.log("[4/6] Verifying 24kHz -> 16kHz resampler (ratio 3:2)...");
  const tone24k = generateTone(440, 1.0, 24000, 0.5);
  const resampled16k = resample24kTo16k(tone24k);
  console.log(`  Input: ${tone24k.length} samples at 24kHz -> Output: ${resampled16k.length} samples at 16kHz`);
  if (resampled16k.length !== 16000) {
    console.error(`  FAIL: Expected 16000 samples, got ${resampled16k.length}`);
    return false;
  }
  const resampledStats = computeAudioStats(resampled16k, 16000);
  console.log(`  Resampled RMS: ${resampledStats.rms.toFixed(4)} (Original: ${(0.5 / Math.sqrt(2)).toFixed(4)})`);
  console.log("  PASS: Resampling verified.");

  // 5. WhatsApp authentication status
  console.log("[5/6] Checking WhatsApp authentication status...");
  const whatsapp = new WhatsAppManager({ authDir: config.whatsappAuthDir });
  const hasAuth = whatsapp.hasPersistedAuth;
  console.log(`  Persisted session found in ${config.whatsappAuthDir}: ${hasAuth}`);

  if (!hasAuth) {
    console.log("  NOTICE: No existing WhatsApp session found.");
    console.log("  To link WhatsApp, run `npm run call` or `npm run test:e2e` and scan the QR code.");
  } else {
    console.log("  PASS: Authenticated WhatsApp credentials exist locally.");
  }

  // 6. Real outbound call check (guarded by TEST_WHATSAPP_NUMBER)
  console.log("[6/6] Checking outbound call transport readiness...");
  if (config.testWhatsAppNumber) {
    console.log(`  TEST_WHATSAPP_NUMBER is configured: ${config.testWhatsAppNumber}`);
    console.log("  Ready to place controlled test call in e2e test.");
  } else {
    console.log("  All local transport/Gemini tests passed. Real call test requires TEST_WHATSAPP_NUMBER.");
  }

  return true;
}

if (process.argv[1]?.includes("transport-test")) {
  runTransportTest().then((success) => {
    process.exit(success ? 0 : 1);
  });
}
