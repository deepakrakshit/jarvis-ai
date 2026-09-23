/**
 * Call summarization and debriefing report generator.
 *
 * Analyzes the completed call transcript and produces an executive
 * after-call summary grounded strictly in the verbatim transcript.
 *
 * Primary Model: Gemini 3.8 Live (Live API debrief synthesis)
 * Fallback Model: Gemini 3.5 Flash Lite (single fallback on limitation/error)
 *
 * Provenance Invariant:
 * Raw transcript is authoritative evidence.
 * Summaries, conditions, and commitments are derived.
 * Placeholders (e.g. "Please refer to transcript") are NEVER valid factual answers.
 */

import { GoogleGenAI, Modality, Type } from "@google/genai";
import { type TranscriptEntry } from "../bridge/realtime-audio.js";
import { type CallMetrics } from "../whatsapp/call.js";

export interface CallReportResult {
  conversationalMessage: string;
  summary: string;
  recipientReply: string;
  callOutcome: string;
  rawTranscript: string;
  conditions: string[];
  commitments: string[];
  actionItems: string[];
}

const DEBRIEF_TOOL_DECLARATION = {
  name: "save_call_record",
  description: "Record structured post-call debrief facts.",
  parameters: {
    type: Type.OBJECT,
    properties: {
      conversationalMessage: {
        type: Type.STRING,
        description: "Natural debriefing message addressing caller as Sir.",
      },
      recipientReply: {
        type: Type.STRING,
        description: "Exact statements and key points spoken by the recipient.",
      },
      callOutcome: {
        type: Type.STRING,
        description: "Short status phrase (e.g. 'Delivered & Confirmed', 'Conditions Stated').",
      },
      summary: {
        type: Type.STRING,
        description: "Concise summary of the interaction based only on the transcript.",
      },
      conditions: {
        type: Type.ARRAY,
        items: { type: Type.STRING },
        description: "List of conditions stated by the recipient.",
      },
      commitments: {
        type: Type.ARRAY,
        items: { type: Type.STRING },
        description: "List of commitments made by either party.",
      },
      actionItems: {
        type: Type.ARRAY,
        items: { type: Type.STRING },
        description: "List of follow-up action items.",
      },
    },
    required: ["conversationalMessage", "recipientReply", "callOutcome", "summary"],
  },
};

export async function generateCallReport(
  apiKey: string,
  callerName: string,
  recipientName: string,
  recipientNumber: string,
  transcript: TranscriptEntry[],
  metrics: CallMetrics,
  modelName = "gemini-3.8-live",
  fallbackModelName = "gemini-3.5-flash-lite"
): Promise<CallReportResult> {
  const formattedTranscript = transcript
    .map((t) => {
      const time = new Date(t.timestamp).toLocaleTimeString();
      const speaker = t.role === "user" ? recipientName || "Recipient" : "JARVIS (AI Assistant)";
      return `[${time}] ${speaker}: ${t.text}`;
    })
    .join("\n");

  const durationSec = (metrics.durationMs / 1000).toFixed(1);

  // Extract raw user utterances directly for strict provenance
  const userUtterances = transcript
    .filter((t) => t.role === "user")
    .map((t) => t.text.trim())
    .filter(Boolean);

  const fallbackUserReply =
    userUtterances.length > 0 ? userUtterances.join("; ") : "No verbal reply detected in call.";

  if (!transcript || transcript.length === 0) {
    const defaultMsg = `Sir, I placed the call to ${recipientName} (+${recipientNumber}), but the call ended after ${durationSec}s without any spoken conversation (reason: ${metrics.endReason || "ended"}).`;
    return {
      conversationalMessage: defaultMsg,
      summary: `Call with ${recipientName} ended without spoken dialogue. Duration: ${durationSec}s. Reason: ${metrics.endReason || "ended"}.`,
      recipientReply: "No verbal reply detected.",
      callOutcome: metrics.endReason || "No dialogue",
      rawTranscript: "No audio dialogue recorded.",
      conditions: [],
      commitments: [],
      actionItems: [],
    };
  }

  const ai = new GoogleGenAI({ apiKey });

  // 1. Primary Attempt: Gemini 3.8 Live session via Live API
  try {
    const liveReport = await generateDebriefWithLive(
      ai,
      modelName,
      callerName,
      recipientName,
      recipientNumber,
      formattedTranscript,
      metrics,
      durationSec
    );

    let parsedReply = String(liveReport.recipientReply || "").trim();
    if (!parsedReply || parsedReply.toLowerCase().includes("verbatim transcript")) {
      parsedReply = fallbackUserReply;
    }

    return {
      conversationalMessage:
        liveReport.conversationalMessage ||
        `Sir, I have completed the call to ${recipientName}. ${parsedReply}`,
      summary:
        liveReport.summary ||
        `Call to ${recipientName} completed in ${durationSec}s. Reply: ${parsedReply}`,
      recipientReply: parsedReply,
      callOutcome: liveReport.callOutcome || "Connected & Completed",
      rawTranscript: formattedTranscript,
      conditions: Array.isArray(liveReport.conditions) ? liveReport.conditions : [],
      commitments: Array.isArray(liveReport.commitments) ? liveReport.commitments : [],
      actionItems: Array.isArray(liveReport.actionItems) ? liveReport.actionItems : [],
    };
  } catch (err: any) {
    const reason = err?.message || String(err);
    console.log(`[Model Fallback] Gemini 3.8 Live lacks required capability: ${reason}`);
    console.log(`[Model Fallback] Using Gemini 3.5 Flash Lite for this specific operation.`);
  }

  // 2. Single Fallback Attempt: Gemini 3.5 Flash Lite via generateContent
  try {
    const prompt = `
You are JARVIS, an autonomous executive AI voice assistant reporting back to ${callerName}.
You just completed an outbound WhatsApp voice call to ${recipientName || "the recipient"} (${recipientNumber}).

Verified verbatim transcript of the call:
${formattedTranscript}

Call Metrics:
- Duration: ${durationSec} seconds
- Disconnect reason: ${metrics.endReason || "normal"}
- Audio interruptions / barge-in handled: ${metrics.interruptionDiscards}

Analyze the call transcript strictly according to the actual words spoken.
Do not fabricate or hallucinate any statements not present in the transcript.
Respond with valid JSON using this exact schema:
{
  "conversationalMessage": "A natural, concise debriefing statement from JARVIS addressing ${callerName} as Sir. State who you called, what they explicitly replied or agreed to, and any conditions mentioned.",
  "recipientReply": "Exact statements and key points spoken by ${recipientName}. Quote or summarize their actual words.",
  "callOutcome": "Short status phrase (e.g. 'Delivered & Confirmed', 'Conditions Stated', 'Message Acknowledged')",
  "summary": "Concise summary of the interaction based only on the transcript.",
  "conditions": ["List of any specific conditions or demands stated by the recipient (e.g. 'Demanded a party before agreeing')"],
  "commitments": ["List of commitments made by either party"],
  "actionItems": ["List of follow-up action items for ${callerName}"]
}
`.trim();

    const response = await ai.models.generateContent({
      model: fallbackModelName,
      contents: prompt,
    });

    const raw = response?.text?.trim() || "";
    const cleanJson = raw.replace(/```json/g, "").replace(/```/g, "").trim();
    const parsed = JSON.parse(cleanJson);

    let parsedReply = String(parsed.recipientReply || "").trim();
    if (!parsedReply || parsedReply.toLowerCase().includes("verbatim transcript")) {
      parsedReply = fallbackUserReply;
    }

    return {
      conversationalMessage:
        parsed.conversationalMessage ||
        `Sir, I have completed the call to ${recipientName}. ${parsedReply}`,
      summary:
        parsed.summary ||
        `Call to ${recipientName} completed in ${durationSec}s. Reply: ${parsedReply}`,
      recipientReply: parsedReply,
      callOutcome: parsed.callOutcome || "Connected & Completed",
      rawTranscript: formattedTranscript,
      conditions: Array.isArray(parsed.conditions) ? parsed.conditions : [],
      commitments: Array.isArray(parsed.commitments) ? parsed.commitments : [],
      actionItems: Array.isArray(parsed.actionItems) ? parsed.actionItems : [],
    };
  } catch (err) {
    // 3. Robust grounded fallback derived strictly from verbatim turns
    const fallbackMsg = `Sir, I completed the WhatsApp call to ${recipientName} (+${recipientNumber}). They stated: "${fallbackUserReply}".`;
    return {
      conversationalMessage: fallbackMsg,
      summary: `Call to ${recipientName} concluded after ${durationSec}s. Spoken input: ${fallbackUserReply}`,
      recipientReply: fallbackUserReply,
      callOutcome: userUtterances.length > 0 ? "Completed with Response" : "Completed without Dialogue",
      rawTranscript: formattedTranscript,
      conditions: [],
      commitments: [],
      actionItems: [],
    };
  }
}

async function generateDebriefWithLive(
  ai: GoogleGenAI,
  model: string,
  callerName: string,
  recipientName: string,
  recipientNumber: string,
  formattedTranscript: string,
  metrics: CallMetrics,
  durationSec: string
): Promise<Partial<CallReportResult>> {
  const prompt = `
You are JARVIS reporting back to ${callerName}.
You just completed an outbound WhatsApp voice call to ${recipientName || "the recipient"} (+${recipientNumber}).

Verified verbatim transcript of the call:
${formattedTranscript}

Call Metrics:
- Duration: ${durationSec} seconds
- Disconnect reason: ${metrics.endReason || "normal"}
- Barge-in interruptions handled: ${metrics.interruptionDiscards}

Analyze the verbatim transcript and invoke the function 'save_call_record' with the structured debrief facts.
`.trim();

  return new Promise(async (resolve, reject) => {
    let resolved = false;
    let session: any = null;

    const timeout = setTimeout(() => {
      if (!resolved) {
        resolved = true;
        if (session) {
          try {
            session.close();
          } catch {}
        }
        reject(new Error("Gemini Live debrief timed out"));
      }
    }, 15000);

    try {
      session = await ai.live.connect({
        model,
        config: {
          responseModalities: [Modality.AUDIO],
          outputAudioTranscription: {},
          tools: [{ functionDeclarations: [DEBRIEF_TOOL_DECLARATION] }],
        },
        callbacks: {
          onmessage: (msg: any) => {
            if (msg.toolCall && Array.isArray(msg.toolCall.functionCalls)) {
              for (const call of msg.toolCall.functionCalls) {
                if (call.name === "save_call_record") {
                  clearTimeout(timeout);
                  resolved = true;
                  try {
                    session.close();
                  } catch {}
                  resolve(call.args as Partial<CallReportResult>);
                  return;
                }
              }
            }
          },
          onerror: (err: any) => {
            if (!resolved) {
              clearTimeout(timeout);
              resolved = true;
              reject(err);
            }
          },
          onclose: (e: any) => {
            if (!resolved) {
              clearTimeout(timeout);
              resolved = true;
              const reason = e?.reason || "Gemini Live session closed before saving call record";
              reject(new Error(reason));
            }
          },
        },
      });

      session.sendClientContent({
        turns: [{ role: "user", parts: [{ text: prompt }] }],
        turnComplete: true,
      });
    } catch (err: any) {
      clearTimeout(timeout);
      if (!resolved) {
        resolved = true;
        reject(err);
      }
    }
  });
}
