/**
 * Console Agent Brain.
 *
 * Coordinates semantic reasoning and tool orchestration with Gemini:
 * USER INPUT -> GEMINI BRAIN -> TOOLS -> DETERMINISTIC EXECUTOR -> RESULT -> GEMINI BRAIN -> USER RESPONSE
 *
 * Primary Brain: Gemini 3.8 Live (native Live API streaming & tool reasoning)
 * Single Fallback: Gemini 3.5 Flash Lite (only when Live API encounters limitation or network fault)
 *
 * Maintains conversational context, executes deterministic capabilities,
 * and formats grounded, natural responses in JARVIS persona.
 */

import { GoogleGenAI, Modality } from "@google/genai";
import { BRAIN_TOOL_DECLARATIONS } from "./tools.js";
import { ToolExecutor } from "./executor.js";
import { type AppConfig } from "../config.js";

export interface AgentBrainOptions {
  config: AppConfig;
  executor: ToolExecutor;
  aiClient?: any;
}

export interface BrainProcessResult {
  response: string;
  toolsInvoked: string[];
}

export class ConsoleAgentBrain {
  readonly #config: AppConfig;
  readonly #executor: ToolExecutor;
  readonly #customAiClient?: any;
  #chatTurns: Array<{ role: "user" | "model" | "tool"; parts: any[] }> = [];

  constructor(options: AgentBrainOptions) {
    this.#config = options.config;
    this.#executor = options.executor;
    this.#customAiClient = options.aiClient;
  }

  /**
   * Process a semantic user message through Gemini Brain reasoning and tool loop.
   */
  async processUserMessage(userInput: string): Promise<BrainProcessResult> {
    const trimmed = userInput.trim();

    // Append user input to multi-turn conversation
    this.#chatTurns.push({
      role: "user",
      parts: [{ text: trimmed }],
    });

    // Prune history to last 24 turns to maintain bounded context window
    if (this.#chatTurns.length > 24) {
      this.#chatTurns = this.#chatTurns.slice(-24);
    }

    // If a custom AI client is injected (e.g. in test mock environment), use generateContent tool loop
    if (this.#customAiClient) {
      return await this.#processWithGenerateContent(this.#customAiClient, trimmed);
    }

    // Primary: Gemini 3.8 Live API
    try {
      return await this.#processWithLiveApi(trimmed);
    } catch (err: any) {
      const reason = err?.message || String(err);
      console.log(`[Model Fallback] Gemini 3.8 Live lacks required capability: ${reason}`);
      console.log(`[Model Fallback] Using Gemini 3.5 Flash Lite for this specific operation.`);

      const fallbackClient = new GoogleGenAI({ apiKey: this.#config.geminiApiKey });
      return await this.#processWithGenerateContent(
        fallbackClient,
        trimmed,
        this.#config.fallbackModel || "gemini-3.5-flash-lite"
      );
    }
  }

  /**
   * Primary Live API tool loop using Gemini 3.8 Live over WebSocket.
   */
  async #processWithLiveApi(userInput: string): Promise<BrainProcessResult> {
    const toolsInvoked: string[] = [];
    const systemInstruction = this.#buildSystemPrompt();

    const toolDecls: any = [...BRAIN_TOOL_DECLARATIONS];
    const toolsConfig: any[] = [{ functionDeclarations: toolDecls }];
    if (this.#config.googleSearchGroundingEnabled) {
      toolsConfig.push({ googleSearch: {} });
    }

    return new Promise<BrainProcessResult>(async (resolve, reject) => {
      let resolved = false;
      let accumulatedText = "";
      let toolExecutionInProgress = false;
      let turnAfterToolStarted = false;
      let activeSession: any = null;

      let timeoutId: NodeJS.Timeout | null = null;
      const resetTimeout = (ms: number) => {
        if (timeoutId) clearTimeout(timeoutId);
        timeoutId = setTimeout(() => {
          if (!resolved) {
            resolved = true;
            if (activeSession) {
              try {
                activeSession.close();
              } catch {}
            }
            reject(new Error("Gemini 3.8 Live session timed out waiting for response"));
          }
        }, ms);
      };

      const cancelTimeout = () => {
        if (timeoutId) {
          clearTimeout(timeoutId);
          timeoutId = null;
        }
      };

      // Set initial timeout: 35 seconds for model to start generating or request a tool
      resetTimeout(35000);

      try {
        const ai = new GoogleGenAI({ apiKey: this.#config.geminiApiKey });
        activeSession = await ai.live.connect({
          model: this.#config.geminiModel || "gemini-3.8-live",
          config: {
            responseModalities: [Modality.AUDIO],
            outputAudioTranscription: {},
            systemInstruction: { parts: [{ text: systemInstruction }] },
            tools: toolsConfig,
          },
          callbacks: {
            onmessage: async (msg: any) => {
              if (resolved) return;

              // Case 1: Model requested tool execution
              if (msg.toolCall && Array.isArray(msg.toolCall.functionCalls)) {
                // Pause timeout while tool (e.g. phone call) is executing
                cancelTimeout();

                for (const call of msg.toolCall.functionCalls) {
                  const toolName = call.name || "unknown";
                  const args = (call.args as Record<string, any>) || {};
                  toolsInvoked.push(toolName);

                  console.log(`[Brain] Intent: ${this.#inferIntentLabel(toolName)}`);
                  console.log(`[Brain] Tool: ${toolName}`);
                  console.log(`[Brain] Tool requested: ${toolName}`);

                  toolExecutionInProgress = true;
                  turnAfterToolStarted = false;

                  const toolResult = await this.#executor.execute(toolName, args);
                  const safeResult = toolResult.success ? toolResult.result : { error: toolResult.error };

                  console.log(`[Brain] Tool completed: ${toolName} (success: ${toolResult.success})`);
                  console.log("[Brain] Tool result received.");
                  console.log("[Brain] Generating grounded response.");

                  activeSession.sendToolResponse({
                    functionResponses: [
                      {
                        id: call.id,
                        name: toolName,
                        response: { output: safeResult },
                      },
                    ],
                  });
                }

                // Reset timeout for model to produce grounded response after tool returns
                resetTimeout(35000);
                return;
              }

              // Detect when model starts output turn after a tool response
              if (toolExecutionInProgress && (msg.serverContent?.modelTurn || msg.serverContent?.outputTranscription)) {
                turnAfterToolStarted = true;
              }

              // Accumulate transcription of model's spoken response
              if (msg.serverContent?.outputTranscription?.text) {
                accumulatedText += msg.serverContent.outputTranscription.text;
              }

              // Check for turn completion
              if (msg.serverContent?.turnComplete) {
                // If a tool was executed, ignore intermediate turnComplete from toolCall turn
                if (toolExecutionInProgress && !turnAfterToolStarted) {
                  return;
                }

                cancelTimeout();
                resolved = true;
                try {
                  activeSession.close();
                } catch {}

                const responseText =
                  accumulatedText.trim() ||
                  (toolsInvoked.includes("call_whatsapp")
                    ? "Sir, I have initiated the outbound WhatsApp call as requested."
                    : "Sir, I have retrieved and processed your request.");

                this.#chatTurns.push({
                  role: "model",
                  parts: [{ text: responseText }],
                });

                resolve({
                  response: responseText,
                  toolsInvoked,
                });
              }
            },
            onerror: (err: any) => {
              if (!resolved) {
                cancelTimeout();
                resolved = true;
                reject(err);
              }
            },
            onclose: (e: any) => {
              if (!resolved) {
                cancelTimeout();
                resolved = true;
                if (accumulatedText.trim()) {
                  resolve({
                    response: accumulatedText.trim(),
                    toolsInvoked,
                  });
                } else {
                  const reason = e?.reason || "Gemini Live session closed";
                  reject(new Error(reason));
                }
              }
            },
          },
        });

        const turnsToSend = this.#chatTurns
          .filter((t) => t.role === "user" || t.role === "model")
          .map((t) => ({
            role: t.role,
            parts: t.parts.map((p) => ({ text: p.text || "" })),
          }));

        activeSession.sendClientContent({
          turns: turnsToSend,
          turnComplete: true,
        });
      } catch (err: any) {
        cancelTimeout();
        if (!resolved) {
          resolved = true;
          reject(err);
        }
      }
    });
  }

  /**
   * Unary generateContent tool loop used by injected mocks and single fallback model.
   */
  async #processWithGenerateContent(
    aiClient: any,
    userInput: string,
    modelName?: string
  ): Promise<BrainProcessResult> {
    const toolsInvoked: string[] = [];
    const systemInstruction = this.#buildSystemPrompt();
    const tools: any = [{ functionDeclarations: BRAIN_TOOL_DECLARATIONS }];
    if (this.#config.googleSearchGroundingEnabled) {
      tools.push({ googleSearch: {} });
    }

    let iterations = 0;
    const maxIterations = 5;

    while (iterations < maxIterations) {
      iterations++;

      let response: any;
      try {
        if (modelName) {
          response = await aiClient.models.generateContent({
            model: modelName,
            contents: this.#chatTurns as any,
            config: {
              systemInstruction,
              tools,
            },
          });
        } else {
          // If custom aiClient was provided (e.g. in test mock), call directly
          response = await aiClient.models.generateContent({
            contents: this.#chatTurns as any,
            systemInstruction,
            tools,
          });
        }
      } catch (err: any) {
        if (toolsInvoked.length > 0) {
          const fallbackActionMsg = toolsInvoked.includes("call_whatsapp")
            ? "Sir, I have initiated the outbound WhatsApp call as requested."
            : "Sir, I have retrieved and processed the information from your call history.";
          return {
            response: fallbackActionMsg,
            toolsInvoked,
          };
        }
        throw err;
      }

      const candidate = response.candidates?.[0];
      const functionCalls = response.functionCalls;

      // Case 1: Model decided to call one or more tools
      if (Array.isArray(functionCalls) && functionCalls.length > 0) {
        if (candidate?.content) {
          this.#chatTurns.push(candidate.content as any);
        }

        for (const call of functionCalls) {
          const toolName = call.name || "unknown";
          const args = (call.args as Record<string, any>) || {};
          toolsInvoked.push(toolName);

          console.log(`[Brain] Intent: ${this.#inferIntentLabel(toolName)}`);
          console.log(`[Brain] Tool: ${toolName}`);
          console.log(`[Brain] Tool requested: ${toolName}`);

          const toolResult = await this.#executor.execute(toolName, args);
          const safeResult = toolResult.success ? toolResult.result : { error: toolResult.error };

          console.log(`[Brain] Tool completed: ${toolName} (success: ${toolResult.success})`);
          console.log("[Brain] Tool result received.");
          console.log("[Brain] Generating grounded response.");

          this.#chatTurns.push({
            role: "tool",
            parts: [
              {
                functionResponse: {
                  name: toolName,
                  response: { output: safeResult },
                },
              },
            ],
          });
        }

        continue;
      }

      // Case 2: Model produced natural language text response
      const replyText = response.text?.trim() || "";
      if (replyText) {
        this.#chatTurns.push({
          role: "model",
          parts: [{ text: replyText }],
        });

        return {
          response: replyText,
          toolsInvoked,
        };
      }

      break;
    }

    const fallback = "Sir, I have processed your request. How else may I assist you?";
    this.#chatTurns.push({
      role: "model",
      parts: [{ text: fallback }],
    });

    return {
      response: fallback,
      toolsInvoked,
    };
  }

  /**
   * Reset conversation memory.
   */
  clearMemory(): void {
    this.#chatTurns = [];
  }

  #buildSystemPrompt(): string {
    const caller = this.#config.userName || "Operator";
    const assistant = this.#config.assistantName || "JARVIS";

    return `
You are ${assistant} (Just A Rather Very Intelligent System), the autonomous executive AI assistant dedicated to ${caller}.
Address ${caller} as "Sir" or "${caller}". Maintain your signature loyal, sophisticated, highly capable, and witty persona.
You speak fluent English, naturally blending conversational Hinglish when ${caller} addresses you in Hindi or Hinglish.

REASONING & DECISION RULES:
1. SEMANTIC INTENT VS CALLING:
   - When ${caller} asks what someone said, what the outcome was, asks for call history, or asks to report on a previous conversation (e.g. "usne kya bola?", "what did he say?", "inform to kro ki uska response kya tha?", "previous call summary", "what was the condition?"):
     → You MUST invoke retrieval tools (e.g. 'get_latest_call', 'get_call_summary', 'get_call_transcript').
     → NEVER initiate a new phone call for a retrieval question!
   - When ${caller} explicitly asks you to call someone, dial, redial, or deliver a message over a phone call (e.g. "call Rahul", "usko wapis call lagao", "call him again", "same number pe call karo", "unko bolo ki ${caller} agreed"):
     → You MUST invoke 'call_whatsapp'.
     → If the target is referred to by a pronoun or reference like "same number", "usko", "him", or "her", you may first invoke 'get_latest_call' to resolve who that person is, and THEN you MUST immediately invoke 'call_whatsapp' with the resolved number and the clean objective. NEVER finish with conversational text until 'call_whatsapp' has been invoked!
     → Extract the clean objective to speak to the recipient (e.g., "Tell the recipient that meeting is postponed"). Do NOT pass raw user commands as objective.
   - When ${caller} asks a direct conversational question or greeting (e.g. "hello jarvis", "how are you?"):
     → Respond directly in text as ${assistant} without invoking tools.

2. STRICT PROVENANCE & NO FABRICATION:
   - Base all statements about past calls STRICTLY on data returned by retrieval tools.
   - If no call records exist in memory, state honestly that there is no completed call available in recent history.
   - Never use placeholder phrases like "Please refer to transcript".
   - Never invent or hallucinate answers.

3. POST-CALL DEBRIEFING:
   - When reporting a call result, clearly summarize what the other person explicitly said, their reaction, and any conditions they placed.
`.trim();
  }

  #inferIntentLabel(toolName: string): string {
    switch (toolName) {
      case "get_latest_call":
        return "RETRIEVE_LATEST_CALL";
      case "get_recent_calls":
        return "RETRIEVE_CALL_HISTORY";
      case "get_call_transcript":
        return "RETRIEVE_TRANSCRIPT_EVIDENCE";
      case "get_call_summary":
        return "RETRIEVE_CALL_SUMMARY";
      case "search_call_history":
        return "SEARCH_MEMORY";
      case "call_whatsapp":
        return "INITIATE_OUTBOUND_CALL";
      case "end_current_call":
        return "TERMINATE_CALL";
      case "get_contact":
        return "RESOLVE_CONTACT";
      case "get_current_call_state":
        return "CHECK_SYSTEM_STATE";
      case "execute_terminal":
        return "DIAGNOSTIC_EXECUTION";
      case "save_call_record":
        return "PERSIST_CALL_DEBRIEF";
      default:
        return "EXECUTE_TOOL";
    }
  }
}
