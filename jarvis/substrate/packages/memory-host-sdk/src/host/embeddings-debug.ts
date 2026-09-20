import { parseBoolean } from "@jarvis/normalization-core/boolean-coercion";
import { normalizeLowercaseStringOrEmpty } from "@jarvis/normalization-core/string-coerce";

// Lightweight debug logging for memory embedding internals.

const normalizedDebugEmbeddings = normalizeLowercaseStringOrEmpty(
  process.env.OPENCLAW_DEBUG_MEMORY_EMBEDDINGS,
);
const debugEmbeddings =
  parseBoolean(normalizedDebugEmbeddings) ?? ["1", "on", "yes"].includes(normalizedDebugEmbeddings);

/** Write embedding debug metadata when OPENCLAW_DEBUG_MEMORY_EMBEDDINGS is enabled. */
export function debugEmbeddingsLog(message: string, meta?: Record<string, unknown>): void {
  if (!debugEmbeddings) {
    return;
  }
  const suffix = meta ? ` ${JSON.stringify(meta)}` : "";
  console.warn(`${message}${suffix}`);
}
