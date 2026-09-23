/**
 * Lazy public SDK facade for active memory search manager lifecycle operations.
 */
import type { JarvisConfig } from "../config/types.jarvis.js";
import type { MemorySearchManager } from "../memory-host-sdk/host/types.js";

type ActiveMemorySearchPurpose = "default" | "status";

/** Active manager lookup result, including a soft error when memory is unavailable. */
export type ActiveMemorySearchManagerResult = {
  manager: MemorySearchManager | null;
  error?: string;
};

type MemoryHostSearchRuntimeModule = typeof import("./memory-host-search.runtime.js");

async function loadMemoryHostSearchRuntime(): Promise<MemoryHostSearchRuntimeModule> {
  return await import("./memory-host-search.runtime.js");
}

/** Loads the active memory search manager for one agent and purpose. */
export async function getActiveMemorySearchManager(params: {
  cfg: JarvisConfig;
  agentId: string;
  purpose?: ActiveMemorySearchPurpose;
}): Promise<ActiveMemorySearchManagerResult> {
  const runtime = await loadMemoryHostSearchRuntime();
  return await runtime.getActiveMemorySearchManager(params);
}

/** Closes every active memory search manager for the provided config. */
export async function closeActiveMemorySearchManagers(cfg?: JarvisConfig): Promise<void> {
  const runtime = await loadMemoryHostSearchRuntime();
  await runtime.closeActiveMemorySearchManagers(cfg);
}

/** Closes the active memory search manager for one agent. */
export async function closeActiveMemorySearchManager(params: {
  cfg: JarvisConfig;
  agentId: string;
}): Promise<void> {
  const runtime = await loadMemoryHostSearchRuntime();
  await runtime.closeActiveMemorySearchManager(params);
}
