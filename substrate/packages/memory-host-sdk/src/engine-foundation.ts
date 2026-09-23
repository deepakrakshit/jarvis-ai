// Real workspace contract for memory engine foundation concerns.

export {
  resolveAgentContextLimits,
  resolveAgentDir,
  resolveAgentWorkspaceDir,
  resolveDefaultAgentId,
  resolveSessionAgentId,
} from "./host/jarvis-runtime-agent.js";
export {
  resolveMemorySearchConfig,
  resolveMemorySearchSyncConfig,
  type ResolvedMemorySearchConfig,
  type ResolvedMemorySearchSyncConfig,
} from "./host/jarvis-runtime-agent.js";
export { parseDurationMs } from "./host/jarvis-runtime-config.js";
export { loadConfig } from "./host/jarvis-runtime-session.js";
export { resolveStateDir } from "./host/jarvis-runtime-config.js";
export { resolveSessionTranscriptsDirForAgent } from "./host/jarvis-runtime-config.js";
export {
  hasConfiguredSecretInput,
  normalizeResolvedSecretInputString,
} from "./host/jarvis-runtime-config.js";
export { root } from "./host/jarvis-runtime-io.js";
export { isPathInside } from "./host/fs-utils.js";
export { createSubsystemLogger } from "./host/jarvis-runtime-io.js";
export { detectMime } from "./host/jarvis-runtime-io.js";
export { resolveGlobalSingleton } from "./host/jarvis-runtime-io.js";
export { onSessionTranscriptUpdate } from "./host/jarvis-runtime-session.js";
export { splitShellArgs } from "./host/jarvis-runtime-io.js";
export { runTasksWithConcurrency } from "./host/jarvis-runtime-io.js";
export {
  shortenHomeInString,
  shortenHomePath,
  resolveUserPath,
  truncateUtf16Safe,
} from "./host/jarvis-runtime-io.js";
export type { JarvisConfig } from "./host/jarvis-runtime-config.js";
export type { SecretInput } from "./host/jarvis-runtime-config.js";
export type { MemoryCitationsMode } from "./host/jarvis-runtime-config.js";
export type { MemorySearchConfig } from "./host/jarvis-runtime-config.js";
