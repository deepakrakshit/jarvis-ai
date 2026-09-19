// Substrate origin: src/acp/permission-relay.ts
// Bridges Gateway approval events into ACP request_permission payloads and outcomes.

export type GatewayExecApprovalDecision = "allow-once" | "allow-always" | "deny";

export interface GatewayExecApprovalEvent {
  approvalId: string;
  command?: string;
  host?: string;
  title?: string;
  toolCallId?: string;
}

export interface PermissionOption {
  optionId: string;
  name: string;
  kind: "allow_once" | "allow_always" | "reject_once";
}

export interface RequestPermissionRequest {
  sessionId: string;
  toolCall: {
    toolCallId: string;
    title: string;
    kind: string;
    status: string;
    rawInput: Record<string, string>;
    _meta?: Record<string, unknown>;
  };
  options: PermissionOption[];
}

export interface RequestPermissionResponse {
  outcome: {
    outcome: "selected" | "cancelled";
    optionId?: string;
  };
}

export function buildAcpPermissionOptions(
  decisions: readonly GatewayExecApprovalDecision[],
): PermissionOption[] {
  const unique = new Set<GatewayExecApprovalDecision>(decisions);
  const options: PermissionOption[] = [];
  if (unique.has("allow-once")) {
    options.push({ optionId: "allow-once", name: "Allow once", kind: "allow_once" });
  }
  if (unique.has("allow-always")) {
    options.push({ optionId: "allow-always", name: "Allow always", kind: "allow_always" });
  }
  if (unique.has("deny")) {
    options.push({ optionId: "deny", name: "Deny", kind: "reject_once" });
  }
  return options;
}
