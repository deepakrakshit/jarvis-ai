// Focused runtime contract for memory plugin config/state/helpers.

export type { AnyAgentTool } from "./host/jarvis-runtime-agent.js";
export { resolveCronStyleNow } from "./host/jarvis-runtime-agent.js";
export { DEFAULT_AGENT_COMPACTION_RESERVE_TOKENS_FLOOR } from "./host/jarvis-runtime-agent.js";
export { resolveDefaultAgentId, resolveSessionAgentId } from "./host/jarvis-runtime-agent.js";
export { resolveMemorySearchConfig } from "./host/jarvis-runtime-agent.js";
export {
  asToolParamsRecord,
  jsonResult,
  readNumberParam,
  readStringParam,
} from "./host/jarvis-runtime-agent.js";
export { SILENT_REPLY_TOKEN } from "./host/jarvis-runtime-session.js";
export { parseNonNegativeByteSize } from "./host/jarvis-runtime-config.js";
export {
  getRuntimeConfig,
  /** @deprecated Use getRuntimeConfig(), or pass the already loaded config through the call path. */
  loadConfig,
} from "./host/jarvis-runtime-session.js";
export { resolveStateDir } from "./host/jarvis-runtime-config.js";
export { resolveSessionTranscriptsDirForAgent } from "./host/jarvis-runtime-config.js";
export { emptyPluginConfigSchema } from "./host/jarvis-runtime-memory.js";
export {
  buildActiveMemoryPromptSection,
  getMemoryCapabilityRegistration,
  listActiveMemoryPublicArtifacts,
} from "./host/jarvis-runtime-memory.js";
export { parseAgentSessionKey } from "./host/jarvis-runtime-agent.js";
export type { JarvisConfig } from "./host/jarvis-runtime-config.js";
export type { MemoryCitationsMode } from "./host/jarvis-runtime-config.js";
export type {
  MemoryFlushPlan,
  MemoryFlushPlanResolver,
  MemoryPluginCapability,
  MemoryPluginPublicArtifact,
  MemoryPluginPublicArtifactsProvider,
  MemoryPluginRuntime,
  MemoryPromptSectionBuilder,
} from "./host/jarvis-runtime-memory.js";
export type { JarvisPluginApi } from "./host/jarvis-runtime-memory.js";
