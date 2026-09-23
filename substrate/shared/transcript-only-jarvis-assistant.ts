// Identifies JARVIS-authored assistant rows that are transcript bookkeeping,
// not provider model output. Some history surfaces keep gateway-injected rows
// visible, so use the narrower delivery-mirror predicate when visibility matters.
export const JARVIS_TRANSCRIPT_ARTIFACT_API = "jarvis-transcript" as const;
export const JARVIS_TRANSCRIPT_ARTIFACT_PROVIDER = "jarvis" as const;
export const JARVIS_DELIVERY_MIRROR_MODEL = "delivery-mirror" as const;
export const CRON_DIRECT_DELIVERY_CONTEXT_KIND = "cron-direct-delivery-context" as const;
const JARVIS_GATEWAY_INJECTED_MODEL = "gateway-injected" as const;

const TRANSCRIPT_ONLY_JARVIS_ASSISTANT_MODELS = new Set<string>([
  JARVIS_DELIVERY_MIRROR_MODEL,
  JARVIS_GATEWAY_INJECTED_MODEL,
]);
const JARVIS_DELIVERY_MIRROR_KINDS = new Set([
  "channel-final",
  "channel-final-suppressed",
  "message-tool-source-reply",
  CRON_DIRECT_DELIVERY_CONTEXT_KIND,
]);

function isJARVISDeliveryMirrorMarker(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const kind = (value as { kind?: unknown }).kind;
  return typeof kind === "string" && JARVIS_DELIVERY_MIRROR_KINDS.has(kind);
}

export function isTranscriptOnlyJARVISAssistantModel(provider: unknown, model: unknown): boolean {
  return (
    provider === JARVIS_TRANSCRIPT_ARTIFACT_PROVIDER &&
    typeof model === "string" &&
    TRANSCRIPT_ONLY_JARVIS_ASSISTANT_MODELS.has(model)
  );
}

/**
 * Returns true when the message is an JARVIS-authored transcript artifact
 * that must not be replayed to providers.
 *
 * Primary check: provider="jarvis" + model in known transcript-only set.
 * Fallback: a valid jarvisDeliveryMirror marker catches observed historical
 * rows whose provider/model provenance was stripped (#99470).
 */
export function isTranscriptOnlyJARVISAssistantMessage(message: unknown): boolean {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return false;
  }
  const entry = message as {
    role?: unknown;
    provider?: unknown;
    model?: unknown;
    jarvisDeliveryMirror?: unknown;
  };
  if (entry.role !== "assistant") {
    return false;
  }
  if (isTranscriptOnlyJARVISAssistantModel(entry.provider, entry.model)) {
    return true;
  }
  return isJARVISDeliveryMirrorMarker(entry.jarvisDeliveryMirror);
}

export function isJARVISMessageToolMirrorAssistantMessage(message: unknown): boolean {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return false;
  }
  const entry = message as { role?: unknown; jarvisMessageToolMirror?: unknown };
  return entry.role === "assistant" && entry.jarvisMessageToolMirror !== undefined;
}

export function isJARVISDeliveryMirrorAssistantMessage(message: unknown): boolean {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return false;
  }
  const entry = message as { role?: unknown; provider?: unknown; model?: unknown };
  return (
    entry.role === "assistant" &&
    entry.provider === JARVIS_TRANSCRIPT_ARTIFACT_PROVIDER &&
    entry.model === JARVIS_DELIVERY_MIRROR_MODEL
  );
}
