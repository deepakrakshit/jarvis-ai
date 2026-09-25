/**
 * Agent Brain Tool Declarations.
 *
 * Exposes deterministic capabilities to Gemini Brain:
 * 1. Memory retrieval tools (get_latest_call, get_recent_calls, get_call_by_id, search_call_history, get_call_transcript, get_call_summary, get_latest_call_by_target)
 * 2. Contact resolution (get_contact)
 * 3. State-changing VoIP control (call_whatsapp, end_current_call, get_current_call_state)
 */

import { Type } from "@google/genai";

export const BRAIN_TOOL_DECLARATIONS = [
  {
    name: "get_latest_call",
    description:
      "Retrieve the most recent completed WhatsApp voice call record from memory. Use this whenever the user asks 'usne kya bola?', 'what did he/she say?', 'what happened in the last call?', 'who did I just talk to?', 'inform to kro ki uska response kya tha', or asks about previous call results. Returns structured facts, recipient reply, conditions, commitments, and summary.",
    parameters: {
      type: Type.OBJECT,
      properties: {},
    },
  },
  {
    name: "get_recent_calls",
    description:
      "Retrieve an overview of the N most recent completed WhatsApp calls. Use when the user asks for call history or a list of recent calls.",
    parameters: {
      type: Type.OBJECT,
      properties: {
        limit: {
          type: Type.NUMBER,
          description: "Maximum number of recent calls to retrieve (default: 5).",
        },
      },
    },
  },
  {
    name: "get_call_by_id",
    description: "Retrieve a specific call record by its unique call ID.",
    parameters: {
      type: Type.OBJECT,
      properties: {
        callId: {
          type: Type.STRING,
          description: "The unique identifier of the call.",
        },
      },
      required: ["callId"],
    },
  },
  {
    name: "search_call_history",
    description:
      "Search call history by freeform keyword across contact names, phone numbers, topics, objectives, or summaries. Use when user asks about a specific past topic, person, or conversation.",
    parameters: {
      type: Type.OBJECT,
      properties: {
        query: {
          type: Type.STRING,
          description: "The search keyword or topic (e.g. 'hackathon', 'party', 'Rahul').",
        },
      },
      required: ["query"],
    },
  },
  {
    name: "get_call_transcript",
    description:
      "Retrieve the verbatim chronological speech transcript for a call. Use when exact quotes or turn-by-turn evidence is needed. If callId is omitted, retrieves the latest call's transcript.",
    parameters: {
      type: Type.OBJECT,
      properties: {
        callId: {
          type: Type.STRING,
          description: "Optional call ID. If omitted, retrieves verbatim transcript of the most recent call.",
        },
      },
    },
  },
  {
    name: "get_call_summary",
    description:
      "Retrieve the grounded summary, outcome, recipient commitments, conditions, and action items for a call. If callId is omitted, retrieves the latest call summary.",
    parameters: {
      type: Type.OBJECT,
      properties: {
        callId: {
          type: Type.STRING,
          description: "Optional call ID. If omitted, retrieves summary of the most recent call.",
        },
      },
    },
  },
  {
    name: "get_latest_call_by_target",
    description:
      "Retrieve the most recent call made to a specific person or phone number. Use when user refers to a specific contact (e.g., 'Rahul se pichli baar kya baat hui thi?').",
    parameters: {
      type: Type.OBJECT,
      properties: {
        target: {
          type: Type.STRING,
          description: "Contact name or phone number.",
        },
      },
      required: ["target"],
    },
  },
  {
    name: "get_contact",
    description:
      "Look up and resolve a contact name or phone number without placing a call. Returns normalized phone number and formatted E.164 string.",
    parameters: {
      type: Type.OBJECT,
      properties: {
        nameOrNumber: {
          type: Type.STRING,
          description: "The contact name or raw phone number to resolve.",
        },
      },
      required: ["nameOrNumber"],
    },
  },
  {
    name: "call_whatsapp",
    description:
      "STATE-CHANGING: Initiate an outbound 1:1 WhatsApp VoIP voice call to a recipient. ONLY invoke this when the user's explicit intent is to place a phone call or redial. If the user refers to 'usko', 'him', 'her', or 'same number', resolve the target from latest call memory. Do NOT pass raw user commands as objective; extract the clean message to convey.",
    parameters: {
      type: Type.OBJECT,
      properties: {
        target: {
          type: Type.STRING,
          description:
            "The phone number or contact name to call. If referring to previous caller ('him', 'usko', 'same number'), resolve from memory.",
        },
        objective: {
          type: Type.STRING,
          description:
            "The extracted semantic goal or message to communicate to the recipient on the user's behalf during the live VoIP call.",
        },
        conversationMode: {
          type: Type.STRING,
          description:
            "Conversation mode for the call: 'MESSAGE_DELIVERY' (concise message), 'INFORMATION_GATHERING' (asking questions and listening), or 'LONG_CONVERSATION' (detailed interactive discussion).",
        },
      },
      required: ["target", "objective"],
    },
  },
  {
    name: "end_current_call",
    description: "Hang up and terminate the currently active WhatsApp voice call, if any.",
    parameters: {
      type: Type.OBJECT,
      properties: {},
    },
  },
  {
    name: "get_current_call_state",
    description:
      "Check whether a WhatsApp call is currently active, dialing, ringing, or idle. Use to verify operational readiness.",
    parameters: {
      type: Type.OBJECT,
      properties: {},
    },
  },
  {
    name: "execute_terminal",
    description:
      "Execute a safe diagnostic or local command related to testing or status checking. Command runs behind strict deterministic policy controls (destructive commands are strictly blocked).",
    parameters: {
      type: Type.OBJECT,
      properties: {
        command: {
          type: Type.STRING,
          description: "The diagnostic or inspection command to execute (e.g. 'git status', 'node -v').",
        },
      },
      required: ["command"],
    },
  },
  {
    name: "save_call_record",
    description:
      "Store structured facts, summary, and debriefing information for a completed call into CallHistory.",
    parameters: {
      type: Type.OBJECT,
      properties: {
        conversationalMessage: {
          type: Type.STRING,
          description: "Debrief message for the user.",
        },
        recipientReply: {
          type: Type.STRING,
          description: "Exact statements spoken by the recipient.",
        },
        callOutcome: {
          type: Type.STRING,
          description: "Outcome of the call.",
        },
        summary: {
          type: Type.STRING,
          description: "Concise summary of the interaction.",
        },
        conditions: {
          type: Type.ARRAY,
          items: { type: Type.STRING },
          description: "Conditions stated by the recipient.",
        },
        commitments: {
          type: Type.ARRAY,
          items: { type: Type.STRING },
          description: "Commitments made during the call.",
        },
        actionItems: {
          type: Type.ARRAY,
          items: { type: Type.STRING },
          description: "Follow-up action items.",
        },
      },
      required: ["conversationalMessage", "recipientReply", "callOutcome", "summary"],
    },
  },
];
