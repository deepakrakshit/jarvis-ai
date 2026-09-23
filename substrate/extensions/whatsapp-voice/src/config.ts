/**
 * Dynamic configuration loader for WhatsApp-Gemini-Voice.
 *
 * All parameters are driven by environment variables and defaults.
 * Secrets are masked and never exposed to logs or terminal output.
 */

import { config as loadDotenv } from "dotenv";
import { resolve, dirname } from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Dynamically locate root workspace .env
const findRootEnv = (): string | undefined => {
  let curr = process.cwd();
  for (let i = 0; i < 5; i++) {
    const candidate = resolve(curr, ".env");
    if (existsSync(candidate)) return candidate;
    curr = dirname(curr);
  }
  return undefined;
};

const rootEnv = findRootEnv();
if (rootEnv) {
  loadDotenv({ path: rootEnv });
} else {
  loadDotenv();
}

export interface AppConfig {
  geminiApiKey: string;
  geminiModel: string;          // Primary model: "gemini-3.8-live"
  fallbackModel: string;        // Dedicated fallback model
  googleSearchGroundingEnabled: boolean;
  geminiRetryAttempts: number;
  geminiRetryBaseDelayMs: number;
  voiceDefaultName: string;
  userName: string;
  assistantName: string;
  defaultCountryCode: string;
  whatsappAuthDir: string;
  callDurationMs: number;
  testWhatsAppNumber?: string;
  whatsappSampleRate: number;
  geminiSampleRate: number;
  geminiOutputSampleRate: number;
  chunkFrames: number;
  logsDir: string;
  recordingsDir: string;
  callHistoryFile: string;
  contactsFilePath: string;
  farewellGracePeriodMs: number;
  conversationMode: string;
  callLanguage: string;
  workspaceDir: string;
}

export function loadConfig(): AppConfig {
  const geminiApiKey = process.env.GEMINI_API_KEY?.trim() ?? "";
  if (!geminiApiKey) {
    throw new Error(
      "GEMINI_API_KEY is not defined in environment or .env file. Please check configuration."
    );
  }

  const geminiModel =
    process.env.MODEL_MAP_GEMINI_LIVE?.trim() ||
    process.env.GEMINI_MODEL?.trim() ||
    "gemini-3.8-live";
  const fallbackModel =
    process.env.MODEL_MAP_GEMINI_3_5_FLASH_LITE?.trim() ||
    process.env.GEMINI_FALLBACK_MODEL?.trim() ||
    "gemini-3.5-flash-lite";
  const googleSearchGroundingEnabled = process.env.GOOGLE_SEARCH_GROUNDING_ENABLED === "true";
  const geminiRetryAttempts = Number(process.env.GEMINI_RETRY_ATTEMPTS) || 3;
  const geminiRetryBaseDelayMs = Number(process.env.GEMINI_RETRY_BASE_DELAY_MS) || 2000;
  const voiceDefaultName = process.env.VOICE_DEFAULT_NAME?.trim() || "Algenib";

  // Dynamic Identity & Persona - Never Hardcoded
  const userName =
    process.env.USER_NAME?.trim() ||
    process.env.USER_CALLSIGN?.trim() ||
    "Operator";
  const assistantName =
    process.env.APP_NAME?.trim() ||
    process.env.ASSISTANT_NAME?.trim() ||
    "JARVIS";
  const defaultCountryCode = process.env.DEFAULT_COUNTRY_CODE?.trim() || "91";
  const callLanguage = process.env.WHATSAPP_CALL_LANGUAGE?.trim() || "hinglish";

  // Dynamic Workspace Directories - Never Hardcoded
  const workspaceDir = resolve(
    process.env.WORKSPACE_DIR?.trim() ||
    process.env.PROJECT_ROOT?.trim() ||
    resolve(__dirname, "../../../..")
  );
  const localExtensionAuth = resolve(__dirname, "../auth");
  const workspaceDataAuth = resolve(workspaceDir, "data", "whatsapp", "auth");
  const resolvedDefaultAuth = existsSync(localExtensionAuth) ? localExtensionAuth : workspaceDataAuth;
  const whatsappAuthDir = resolve(process.env.WHATSAPP_AUTH_DIR?.trim() || resolvedDefaultAuth);

  const callDurationMs = Number(process.env.CALL_DURATION_MS) || Number(process.env.WHATSAPP_CALL_TIMEOUT_MS) || 120_000;
  const testWhatsAppNumber = process.env.TEST_WHATSAPP_NUMBER?.trim() || undefined;

  const whatsappSampleRate = Number(process.env.WHATSAPP_SAMPLE_RATE) || 16_000;
  const geminiSampleRate = Number(process.env.GEMINI_INPUT_SAMPLE_RATE) || 16_000;
  const geminiOutputSampleRate = Number(process.env.GEMINI_OUTPUT_SAMPLE_RATE) || 24_000;
  const chunkFrames = Number(process.env.AUDIO_CHUNK_FRAMES) || 320;

  const defaultLogsDir = resolve(workspaceDir, "data", "whatsapp", "logs");
  const defaultRecordingsDir = resolve(workspaceDir, "data", "whatsapp", "recordings");
  const logsDir = resolve(process.env.LOGS_DIR?.trim() || defaultLogsDir);
  const recordingsDir = resolve(process.env.RECORDINGS_DIR?.trim() || defaultRecordingsDir);
  const callHistoryFile = resolve(
    process.env.CALL_HISTORY_FILE?.trim() || resolve(logsDir, "call-history.json")
  );
  const contactsFilePath = resolve(
    process.env.WHATSAPP_CONTACTS_FILE?.trim() || resolve(workspaceDir, "data", "whatsapp", "contacts.json")
  );

  const farewellGracePeriodMs = Number(process.env.FAREWELL_GRACE_PERIOD_MS) || 2500;
  const conversationMode = process.env.CONVERSATION_MODE?.trim() || process.env.WHATSAPP_CONVERSATION_MODE?.trim() || "MESSAGE_DELIVERY";

  return {
    geminiApiKey,
    geminiModel,
    fallbackModel,
    googleSearchGroundingEnabled,
    geminiRetryAttempts,
    geminiRetryBaseDelayMs,
    voiceDefaultName,
    userName,
    assistantName,
    defaultCountryCode,
    whatsappAuthDir,
    callDurationMs,
    testWhatsAppNumber,
    whatsappSampleRate,
    geminiSampleRate,
    geminiOutputSampleRate,
    chunkFrames,
    logsDir,
    recordingsDir,
    callHistoryFile,
    contactsFilePath,
    farewellGracePeriodMs,
    conversationMode,
    callLanguage,
    workspaceDir,
  };
}

/**
 * Return safe diagnostic representation with masked credentials.
 */
export function getSafeConfigSummary(config: AppConfig): Record<string, unknown> {
  return {
    geminiModel: config.geminiModel,
    fallbackModel: config.fallbackModel,
    voiceDefaultName: config.voiceDefaultName,
    geminiApiKeyConfigured: Boolean(config.geminiApiKey),
    whatsappAuthDir: config.whatsappAuthDir,
    callDurationMs: config.callDurationMs,
    testWhatsAppNumberConfigured: Boolean(config.testWhatsAppNumber),
    whatsappSampleRate: config.whatsappSampleRate,
    geminiSampleRate: config.geminiSampleRate,
    geminiOutputSampleRate: config.geminiOutputSampleRate,
    chunkFrames: config.chunkFrames,
    logsDir: config.logsDir,
    recordingsDir: config.recordingsDir,
  };
}
