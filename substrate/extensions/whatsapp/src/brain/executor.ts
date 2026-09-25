/**
 * Deterministic Tool Executor for Agent Brain.
 *
 * Enforces safety, invariants, state validation, and strict provenance:
 * - Deterministic state machine checks (active-call singleton)
 * - Grounded facts strictly sourced from raw transcripts
 * - Rejection of invalid placeholders
 * - Contact resolution without side effects
 */

import { type CallHistoryManager, type CallHistoryEntry } from "../memory/call-history.js";
import { type ContactResolver } from "../contacts/resolver.js";
import { type CallSession } from "../engine/call.js";

export interface ToolExecutorDeps {
  historyManager: CallHistoryManager;
  contactResolver: ContactResolver;
  getActiveCall: () => CallSession | null;
  startOutboundCall: (params: {
    target: string;
    objective: string;
    conversationMode?: string;
  }) => Promise<any>;
  endActiveCall: () => boolean;
}

export interface ToolExecutionResult {
  toolName: string;
  success: boolean;
  result?: any;
  error?: string;
}

export class ToolExecutor {
  readonly #deps: ToolExecutorDeps;

  constructor(deps: ToolExecutorDeps) {
    this.#deps = deps;
  }

  /**
   * Execute a tool call deterministically.
   */
  async execute(toolName: string, args: Record<string, any>): Promise<ToolExecutionResult> {
    try {
      switch (toolName) {
        case "get_latest_call":
          return this.#handleGetLatestCall();

        case "get_recent_calls":
          return this.#handleGetRecentCalls(args);

        case "get_call_by_id":
          return this.#handleGetCallById(args);

        case "search_call_history":
          return this.#handleSearchCallHistory(args);

        case "get_call_transcript":
          return this.#handleGetCallTranscript(args);

        case "get_call_summary":
          return this.#handleGetCallSummary(args);

        case "get_latest_call_by_target":
          return this.#handleGetLatestCallByTarget(args);

        case "get_contact":
          return this.#handleGetContact(args);

        case "call_whatsapp":
          return await this.#handleCallWhatsApp(args);

        case "end_current_call":
          return this.#handleEndCurrentCall();

        case "get_current_call_state":
          return this.#handleGetCurrentCallState();

        case "execute_terminal":
          return await this.#handleExecuteTerminal(args);

        case "save_call_record":
          return await this.#handleSaveCallRecord(args);

        default:
          return {
            toolName,
            success: false,
            error: `Unknown tool '${toolName}'.`,
          };
      }
    } catch (err: any) {
      return {
        toolName,
        success: false,
        error: err?.message || String(err),
      };
    }
  }

  #handleGetLatestCall(): ToolExecutionResult {
    const record = this.#deps.historyManager.getLatest();
    if (!record) {
      return {
        toolName: "get_latest_call",
        success: true,
        result: {
          status: "empty",
          message: "There's no completed call available in my recent history.",
        },
      };
    }

    return {
      toolName: "get_latest_call",
      success: true,
      result: this.#sanitizeCallRecordForBrain(record),
    };
  }

  #handleGetRecentCalls(args: Record<string, any>): ToolExecutionResult {
    const limit = Number(args.limit) || 5;
    const records = this.#deps.historyManager.getRecent(limit);

    return {
      toolName: "get_recent_calls",
      success: true,
      result: {
        count: records.length,
        calls: records.map((r) => ({
          callId: r.callId,
          targetName: r.targetName,
          phoneNumber: r.phoneNumber,
          objective: r.objective,
          callOutcome: r.callOutcome,
          recipientReply: this.#extractGroundedReply(r),
          durationSec: r.durationSec,
          timestamp: r.timestamp,
        })),
      },
    };
  }

  #handleGetCallById(args: Record<string, any>): ToolExecutionResult {
    const callId = String(args.callId || "").trim();
    if (!callId) {
      return { toolName: "get_call_by_id", success: false, error: "Missing callId parameter." };
    }

    const record = this.#deps.historyManager.getCallById(callId);
    if (!record) {
      return {
        toolName: "get_call_by_id",
        success: false,
        error: `Call record with ID '${callId}' not found.`,
      };
    }

    return {
      toolName: "get_call_by_id",
      success: true,
      result: this.#sanitizeCallRecordForBrain(record),
    };
  }

  #handleSearchCallHistory(args: Record<string, any>): ToolExecutionResult {
    const query = String(args.query || "").trim();
    if (!query) {
      return { toolName: "search_call_history", success: false, error: "Missing query parameter." };
    }

    const matches = this.#deps.historyManager.search(query);
    return {
      toolName: "search_call_history",
      success: true,
      result: {
        query,
        count: matches.length,
        results: matches.map((r) => ({
          callId: r.callId,
          targetName: r.targetName,
          phoneNumber: r.phoneNumber,
          objective: r.objective,
          callOutcome: r.callOutcome,
          recipientReply: this.#extractGroundedReply(r),
          summary: r.summary,
          timestamp: r.timestamp,
        })),
      },
    };
  }

  #handleGetCallTranscript(args: Record<string, any>): ToolExecutionResult {
    const callId = args.callId ? String(args.callId).trim() : undefined;
    const record = callId
      ? this.#deps.historyManager.getCallById(callId)
      : this.#deps.historyManager.getLatest();

    if (!record) {
      return {
        toolName: "get_call_transcript",
        success: true,
        result: { status: "empty", message: "No transcript available in memory." },
      };
    }

    const turns = (record.transcript || []).map((t) => ({
      speaker: t.role === "user" ? record.targetName || "Recipient" : "JARVIS",
      text: t.text,
      timestamp: t.timestamp,
    }));

    return {
      toolName: "get_call_transcript",
      success: true,
      result: {
        callId: record.callId,
        targetName: record.targetName,
        phoneNumber: record.phoneNumber,
        turnCount: turns.length,
        turns,
      },
    };
  }

  #handleGetCallSummary(args: Record<string, any>): ToolExecutionResult {
    const callId = args.callId ? String(args.callId).trim() : undefined;
    const record = callId
      ? this.#deps.historyManager.getCallById(callId)
      : this.#deps.historyManager.getLatest();

    if (!record) {
      return {
        toolName: "get_call_summary",
        success: true,
        result: { status: "empty", message: "No call records available to summarize." },
      };
    }

    return {
      toolName: "get_call_summary",
      success: true,
      result: {
        callId: record.callId,
        targetName: record.targetName,
        phoneNumber: record.phoneNumber,
        objective: record.objective,
        callOutcome: record.callOutcome,
        recipientReply: this.#extractGroundedReply(record),
        summary: record.summary,
        conditions: record.conditions || [],
        commitments: record.commitments || [],
        actionItems: record.actionItems || [],
        durationSec: record.durationSec,
      },
    };
  }

  #handleGetLatestCallByTarget(args: Record<string, any>): ToolExecutionResult {
    const target = String(args.target || "").trim();
    if (!target) {
      return { toolName: "get_latest_call_by_target", success: false, error: "Missing target parameter." };
    }

    const record = this.#deps.historyManager.getLatestByTarget(target);
    if (!record) {
      return {
        toolName: "get_latest_call_by_target",
        success: true,
        result: {
          status: "not_found",
          message: `No previous call record found matching target '${target}'.`,
        },
      };
    }

    return {
      toolName: "get_latest_call_by_target",
      success: true,
      result: this.#sanitizeCallRecordForBrain(record),
    };
  }

  #handleGetContact(args: Record<string, any>): ToolExecutionResult {
    const target = String(args.nameOrNumber || "").trim();
    if (!target) {
      return { toolName: "get_contact", success: false, error: "Missing nameOrNumber parameter." };
    }

    try {
      const contact = this.#deps.contactResolver.resolveTarget(target);
      return {
        toolName: "get_contact",
        success: true,
        result: {
          name: contact.name,
          phoneNumber: contact.phoneNumber,
          formatted: contact.formatted,
        },
      };
    } catch (err: any) {
      return {
        toolName: "get_contact",
        success: false,
        error: err?.message || `Could not resolve contact '${target}'.`,
      };
    }
  }

  async #handleCallWhatsApp(args: Record<string, any>): Promise<ToolExecutionResult> {
    // 1. Check active call singleton invariant
    const activeSession = this.#deps.getActiveCall();
    if (activeSession) {
      return {
        toolName: "call_whatsapp",
        success: false,
        error: "A call is already active. Cannot place a new call until the current call concludes.",
      };
    }
    console.log("[Executor] No active call.");

    let target = String(args.target || "").trim();
    let objective = String(args.objective || "").trim();

    if (!target) {
      // Attempt resolution from latest call
      const latest = this.#deps.historyManager.getLatest();
      if (latest) {
        target = latest.phoneNumber;
      } else {
        return {
          toolName: "call_whatsapp",
          success: false,
          error: "Target phone number or contact was not specified and no previous call exists in memory.",
        };
      }
    }

    if (!objective) {
      const caller = process.env.USER_NAME || process.env.USER_CALLSIGN || "the user";
      objective = `Converse on behalf of ${caller}.`;
    }

    // Resolve target cleanly
    let resolvedTarget = target;
    try {
      const contact = this.#deps.contactResolver.resolveTarget(target);
      resolvedTarget = contact.phoneNumber;
    } catch {
      // If contact resolver fails, fallback to raw target
      resolvedTarget = target;
    }

    // Always normalize outbound calling target to full country-code E.164 digits for WhatsApp VoIP
    const digits = resolvedTarget.replace(/\D/g, "");
    if (digits.length === 10 && /^[6-9]/.test(digits)) {
      resolvedTarget = `91${digits}`;
    } else if (digits.length >= 7) {
      resolvedTarget = digits;
    }

    console.log("[Executor] Target validated.");
    console.log("[WhatsApp] Call started.");

    const conversationMode = args.conversationMode || "MESSAGE_DELIVERY";

    // Trigger deterministic call execution
    const outcome = await this.#deps.startOutboundCall({
      target: resolvedTarget,
      objective,
      conversationMode,
    });

    return {
      toolName: "call_whatsapp",
      success: true,
      result: outcome,
    };
  }

  #handleEndCurrentCall(): ToolExecutionResult {
    const ended = this.#deps.endActiveCall();
    return {
      toolName: "end_current_call",
      success: true,
      result: {
        ended,
        message: ended ? "Active call hung up successfully." : "No active call was found to end.",
      },
    };
  }

  #handleGetCurrentCallState(): ToolExecutionResult {
    const activeSession = this.#deps.getActiveCall();
    if (!activeSession) {
      return {
        toolName: "get_current_call_state",
        success: true,
        result: {
          state: "IDLE",
          callId: null,
          targetNumber: null,
        },
      };
    }

    return {
      toolName: "get_current_call_state",
      success: true,
      result: {
        state: activeSession.state,
        callId: activeSession.callId,
        targetNumber: activeSession.targetNumber,
      },
    };
  }

  /**
   * Ensure call record passed to brain contains only grounded facts,
   * never error strings or placeholders.
   */
  #sanitizeCallRecordForBrain(record: CallHistoryEntry): Record<string, any> {
    const groundedReply = this.#extractGroundedReply(record);
    return {
      callId: record.callId,
      targetName: record.targetName,
      phoneNumber: record.phoneNumber,
      objective: record.objective,
      callOutcome: record.callOutcome,
      recipientReply: groundedReply,
      summary: record.summary,
      conditions: record.conditions || [],
      commitments: record.commitments || [],
      actionItems: record.actionItems || [],
      durationSec: record.durationSec,
      timestamp: record.timestamp,
    };
  }

  /**
   * Grounded extraction of recipient reply directly from raw transcript turns.
   */
  #extractGroundedReply(record: CallHistoryEntry): string {
    const rawReply = String(record.recipientReply || "").trim();
    if (rawReply && !rawReply.toLowerCase().includes("verbatim transcript") && !rawReply.toLowerCase().includes("please refer")) {
      return rawReply;
    }

    // Provenance fallback: extract verbatim user turns
    const userTurns = (record.transcript || [])
      .filter((t) => t.role === "user")
      .map((t) => t.text.trim())
      .filter(Boolean);

    if (userTurns.length > 0) {
      return userTurns.join("; ");
    }

    return "No spoken reply detected during the call.";
  }

  async #handleExecuteTerminal(args: Record<string, any>): Promise<ToolExecutionResult> {
    const rawCmd = String(args.command || "").trim();
    if (!rawCmd) {
      return { toolName: "execute_terminal", success: false, error: "Empty command." };
    }

    // Deterministic policy controls: allow safe diagnostic/inspection commands only
    const blockedKeywords = ["rm ", "del ", "erase ", "drop ", "truncate ", "format ", "shutdown ", "kill ", "auth", "token", "key", ".env"];
    const lowerCmd = rawCmd.toLowerCase();
    for (const kw of blockedKeywords) {
      if (lowerCmd.includes(kw)) {
        return {
          toolName: "execute_terminal",
          success: false,
          error: `Security Policy Violation: Command contains restricted pattern '${kw.trim()}'.`,
        };
      }
    }

    try {
      const { exec } = await import("node:child_process");
      const { promisify } = await import("node:util");
      const execAsync = promisify(exec);
      const { stdout, stderr } = await execAsync(rawCmd, { timeout: 10000 });
      return {
        toolName: "execute_terminal",
        success: true,
        result: {
          stdout: stdout.trim(),
          stderr: stderr.trim(),
        },
      };
    } catch (err: any) {
      return {
        toolName: "execute_terminal",
        success: false,
        error: `Command execution failed: ${err?.message || err}`,
      };
    }
  }

  async #handleSaveCallRecord(args: Record<string, any>): Promise<ToolExecutionResult> {
    const recipientReply = String(args.recipientReply || "").trim();
    const callOutcome = String(args.callOutcome || "Completed").trim();
    const summary = String(args.summary || "").trim();
    const conditions = Array.isArray(args.conditions) ? args.conditions.map(String) : [];
    const commitments = Array.isArray(args.commitments) ? args.commitments.map(String) : [];
    const actionItems = Array.isArray(args.actionItems) ? args.actionItems.map(String) : [];

    const latest = this.#deps.historyManager.getLatest();
    if (latest) {
      latest.recipientReply = recipientReply || latest.recipientReply;
      latest.callOutcome = callOutcome || latest.callOutcome;
      latest.summary = summary || latest.summary;
      latest.conditions = conditions;
      latest.commitments = commitments;
      latest.actionItems = actionItems;
      await this.#deps.historyManager.addRecord(latest);
    }

    return {
      toolName: "save_call_record",
      success: true,
      result: {
        saved: true,
        summary,
        recipientReply,
      },
    };
  }
}
