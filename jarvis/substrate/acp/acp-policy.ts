// Substrate origin: src/acp/policy.ts
// Policy gates for ACP availability, dispatch, and allowed agent ids.

export interface AcpConfig {
  enabled?: boolean;
  dispatch?: {
    enabled?: boolean;
  };
  allowedAgents?: string[];
}

export class AcpPolicyError extends Error {
  constructor(public code: string, message: string) {
    super(message);
    this.name = "AcpPolicyError";
  }
}

export function isAcpEnabledByPolicy(cfg: { acp?: AcpConfig }): boolean {
  return cfg.acp?.enabled !== false;
}

export function resolveAcpDispatchPolicyError(cfg: { acp?: AcpConfig }): AcpPolicyError | null {
  if (!isAcpEnabledByPolicy(cfg)) {
    return new AcpPolicyError("ACP_DISABLED", "ACP is disabled by policy (`acp.enabled=false`).");
  }
  if (cfg.acp?.dispatch?.enabled === false) {
    return new AcpPolicyError("ACP_DISPATCH_DISABLED", "ACP dispatch is disabled by policy (`acp.dispatch.enabled=false`).");
  }
  return null;
}

export function resolveAcpAgentPolicyError(
  cfg: { acp?: AcpConfig },
  agentId: string,
): AcpPolicyError | null {
  const allowed = (cfg.acp?.allowedAgents ?? []).map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (allowed.length === 0) {
    return null;
  }
  if (!allowed.includes(agentId.trim().toLowerCase())) {
    return new AcpPolicyError(
      "ACP_AGENT_NOT_ALLOWED",
      `ACP agent "${agentId}" is not allowed by policy.`,
    );
  }
  return null;
}
