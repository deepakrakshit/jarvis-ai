// ACP Core type module defines shared TypeScript contracts.
// Substrate origin: packages/acp-core/src/types.ts

const ACP_PROVENANCE_MODE_VALUES = ["off", "meta", "meta+receipt"] as const;

export type SessionId = string;

export type AcpProvenanceMode = (typeof ACP_PROVENANCE_MODE_VALUES)[number];

export function normalizeAcpProvenanceMode(
  value: string | undefined,
): AcpProvenanceMode | undefined {
  if (!value) return undefined;
  const normalized = value.trim().toLowerCase();
  return (ACP_PROVENANCE_MODE_VALUES as readonly string[]).includes(normalized)
    ? (normalized as AcpProvenanceMode)
    : undefined;
}

export type AcpSession = {
  sessionId: SessionId;
  sessionKey: string;
  ledgerSessionId?: string;
  cwd: string;
  createdAt: number;
  lastTouchedAt: number;
  abortController: AbortController | null;
  activeRunId: string | null;
};

export type AcpServerOptions = {
  gatewayUrl?: string;
  gatewayToken?: string;
  gatewayPassword?: string;
  defaultSessionKey?: string;
  defaultSessionLabel?: string;
  requireExistingSession?: boolean;
  resetSession?: boolean;
  prefixCwd?: boolean;
  provenanceMode?: AcpProvenanceMode;
  sessionCreateRateLimit?: {
    maxRequests?: number;
    windowMs?: number;
  };
  verbose?: boolean;
};

export type SessionAcpIdentitySource = "ensure" | "status" | "event";

export type SessionAcpIdentityState = "pending" | "resolved";

export type SessionAcpIdentity = {
  state: SessionAcpIdentityState;
  recordId?: string;
  sessionId?: string;
  agentSessionId?: string;
  source: SessionAcpIdentitySource;
  lastUpdatedAt: number;
};

export type AcpSessionRuntimeOptions = {
  runtimeMode?: string;
  model?: string;
  thinking?: string;
  cwd?: string;
  permissionProfile?: string;
  timeoutSeconds?: number;
  backendExtras?: Record<string, string>;
};

export type SessionAcpMeta = {
  backend: string;
  agent: string;
  runtimeSessionName: string;
  identity?: SessionAcpIdentity;
  mode: "persistent" | "oneshot";
  runtimeOptions?: AcpSessionRuntimeOptions;
  cwd?: string;
  state: "idle" | "running" | "error";
  lastActivityAt: number;
  lastError?: string;
};
