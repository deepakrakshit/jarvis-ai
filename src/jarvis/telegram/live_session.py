"""Dedicated Gemini 3.8 Live Remote Control Session Engine for Telegram.

Enforces Section 8 and Section 58.1 of ARCHITECTURE.md:
Maintains an independent, persistent bidirectional Gemini 3.8 Live session
for Telegram remote control, completely isolated from the PC voice audio pipeline
(zero laptop speaker leakage, zero microphone capture).

Bridges Telegram commands and multi-turn conversational reasoning directly
into the authoritative ActionBroker, PolicyEngine, and execution fabric.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from google import genai
from google.genai import types

from jarvis.actions.broker import ActionBroker, action_broker
from jarvis.cognition.web_search import search_web
from jarvis.config import settings
from jarvis.contracts.action import ActionRequest, ExecutionTarget
from jarvis.contracts.memory import MemoryRecord, MemoryType
from jarvis.memory.manager import memory_manager
from jarvis.policy.firewall import (
    CAPABILITY_APP_LAUNCH,
    CAPABILITY_BROWSER_CLICK,
    CAPABILITY_BROWSER_NAVIGATE,
    CAPABILITY_BROWSER_SCREENSHOT,
    CAPABILITY_BROWSER_SNAPSHOT,
    CAPABILITY_BROWSER_TYPE,
    CAPABILITY_COMPUTER_SCREENSHOT,
    CAPABILITY_FILESYSTEM_LIST,
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_FILESYSTEM_SEARCH,
    CAPABILITY_FILESYSTEM_WRITE,
    CAPABILITY_PROCESS_ENUMERATE,
    CAPABILITY_SHELL_EXECUTE,
    CAPABILITY_SYSTEM_INFO,
    CAPABILITY_SYSTEM_VOLUME,
    CAPABILITY_UI_INSPECT,
    CAPABILITY_UI_INTERACT,
    CAPABILITY_WHATSAPP_CALL,
    CAPABILITY_WHATSAPP_HISTORY,
    CAPABILITY_WHATSAPP_LOGIN,
    CAPABILITY_WHATSAPP_LOGOUT,
    CAPABILITY_WHATSAPP_STATUS,
    CAPABILITY_WINDOW_CLOSE,
    CAPABILITY_WINDOW_FOCUS,
    CAPABILITY_WINDOW_LIST,
)
from jarvis.telemetry import logger


@dataclass
class TelegramLiveTurnResult:
    """Encapsulates the multimodal outcome of a completed Telegram Live turn."""

    text: str = ""
    artifacts: List[Path] = field(default_factory=list)
    tools_called: List[str] = field(default_factory=list)
    error: Optional[str] = None


# Canonical tool specifications exposed to the Telegram Gemini 3.8 Live session
TELEGRAM_LIVE_TOOLS_SPEC: List[Dict[str, Any]] = [
    {
        "name": "filesystem_search",
        "description": (
            "Search for files by keyword query and/or file extension in a directory (default: 'downloads'). "
            "Use this when looking for specific files like 'java pbl', 'presentation', or PowerPoint files."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {
                    "type": "STRING",
                    "description": "Directory name or path (e.g. 'downloads', 'documents', 'desktop'). Defaults to 'downloads'.",
                },
                "query": {
                    "type": "STRING",
                    "description": "Keywords or search term to match against file names (e.g. 'java pbl').",
                },
                "extension": {
                    "type": "STRING",
                    "description": "Optional file extension filter without dot (e.g. 'ppt', 'pptx', 'pdf', 'docx').",
                },
                "max_results": {
                    "type": "INTEGER",
                    "description": "Maximum number of search results to return (default: 20).",
                },
            },
        },
    },
    {
        "name": "filesystem_read",
        "description": "Read text or document contents of a file on the local filesystem.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING", "description": "Path to the file to read."}},
            "required": ["path"],
        },
    },
    {
        "name": "filesystem_list",
        "description": "List files and subdirectories in a directory path.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING", "description": "Directory path to list."}},
            "required": ["path"],
        },
    },
    {
        "name": "filesystem_write",
        "description": "Write text contents to a file on the filesystem.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Path of the file to write."},
                "content": {"type": "STRING", "description": "Text content to write."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "system_screenshot",
        "description": "Capture a screenshot of the current primary Windows desktop display and return it as a photo.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "system_info",
        "description": "Get current host system hardware, CPU, RAM, and operating system status metrics.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "system_volume_get",
        "description": "Get master audio volume level percentage and mute state.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "system_volume_set",
        "description": "Set master audio volume level to target percentage (0-100).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "level": {
                    "type": "INTEGER",
                    "description": "Target volume percentage from 0 to 100.",
                }
            },
            "required": ["level"],
        },
    },
    {
        "name": "process_list",
        "description": "Enumerate active system processes with optional name filtering.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "filter_name": {
                    "type": "STRING",
                    "description": "Optional substring filter on process name.",
                }
            },
        },
    },
    {
        "name": "browser_navigate",
        "description": "Navigate to a URL using the autonomous browser to inspect contents or play media (e.g. YouTube).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING", "description": "The URL to navigate to."}},
            "required": ["url"],
        },
    },
    {
        "name": "browser_snapshot",
        "description": "Capture structured text, links, and accessibility tree of the current webpage to inspect elements.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "max_chars": {
                    "type": "INTEGER",
                    "description": "Maximum characters of text to return (default: 20000).",
                }
            },
        },
    },
    {
        "name": "browser_click",
        "description": "Click an element on the current webpage using a CSS selector or text selector.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "selector": {
                    "type": "STRING",
                    "description": "CSS selector, text selector, or button identifier.",
                }
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_type",
        "description": "Type text into an input field or search bar on the current webpage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "selector": {
                    "type": "STRING",
                    "description": "CSS selector for the input field.",
                },
                "text": {
                    "type": "STRING",
                    "description": "The text string to type into the field.",
                },
                "press_enter": {
                    "type": "BOOLEAN",
                    "description": "Whether to press Enter after typing to submit form or search (default: true).",
                },
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "browser_screenshot",
        "description": "Capture a viewport screenshot of the current webpage.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "app_launch",
        "description": "Launch an installed Windows application or registered protocol handler asynchronously.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {
                    "type": "STRING",
                    "description": "Application name (e.g. 'Notepad', 'Spotify', 'Paint') or executable path.",
                },
                "arguments": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Optional command line arguments to pass to the application.",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "app_focus",
        "description": "Bring an application window cleanly to the foreground.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {
                    "type": "STRING",
                    "description": "Window title or application name to focus.",
                }
            },
            "required": ["target"],
        },
    },
    {
        "name": "app_close",
        "description": "Close an application window gracefully.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {
                    "type": "STRING",
                    "description": "Window title or application name to close.",
                }
            },
            "required": ["target"],
        },
    },
    {
        "name": "window_list",
        "description": "Enumerate all open desktop application windows with titles and process IDs.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "ui_inspect",
        "description": "Inspect the live interactive UI automation tree of an application window.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "window_target": {
                    "type": "STRING",
                    "description": "Optional window title or application name.",
                },
                "max_depth": {
                    "type": "INTEGER",
                    "description": "Maximum tree traversal depth (default: 5).",
                },
            },
        },
    },
    {
        "name": "ui_interact",
        "description": "Interact with an in-app UI control using Microsoft UI Automation patterns.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "window_target": {
                    "type": "STRING",
                    "description": "Window title or application name containing the control.",
                },
                "element_query": {
                    "type": "STRING",
                    "description": "Control name, label, or AutomationId.",
                },
                "action": {
                    "type": "STRING",
                    "description": "Action to perform: 'click', 'set_value', 'toggle', 'select', 'scroll'.",
                },
                "value": {
                    "type": "STRING",
                    "description": "Text value to enter if action is 'set_value' or 'type'.",
                },
            },
            "required": ["window_target", "element_query"],
        },
    },
    {
        "name": "whatsapp_call",
        "description": (
            "Initiate an autonomous full-duplex WhatsApp voice call to a contact or phone number on behalf of the user. "
            "This tool BLOCKS until the phone call finishes and dialogue concludes. "
            "When this tool returns, the call is ALREADY FINISHED and the result contains 'recipient_reply', 'summary', and 'outcome_message'. "
            "IMMEDIATELY report the recipient's reply and call outcome to the user."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {
                    "type": "STRING",
                    "description": "Target contact name or phone number with country code (e.g. 'John', '+919876543210').",
                },
                "objective": {
                    "type": "STRING",
                    "description": "The exact objective, message to deliver, or question to ask the recipient.",
                },
                "conversation_mode": {
                    "type": "STRING",
                    "description": "Optional conversation style: 'MESSAGE_DELIVERY', 'CONVERSATIONAL', or 'EMERGENCY'.",
                },
            },
            "required": ["target", "objective"],
        },
    },
    {
        "name": "whatsapp_get_latest_call",
        "description": "Retrieve the status, summary, recipient response, and outcome of past completed WhatsApp phone calls.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "whatsapp_status",
        "description": "Check WhatsApp voice calling engine status, session authentication state, and configuration.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "whatsapp_login",
        "description": "Generate and display a WhatsApp Web QR code for device authentication/login.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "force_refresh": {
                    "type": "BOOLEAN",
                    "description": "Whether to force clear previous session and generate a new QR code.",
                }
            },
        },
    },
    {
        "name": "whatsapp_logout",
        "description": "Log out and purge stored WhatsApp Web session authentication credentials.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "shell_execute",
        "description": "Execute a PowerShell command on the host Windows system within policy boundaries.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "PowerShell command line to execute."}
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the live web for current real-time information, news, weather, documentation, or facts.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "The search query keywords to search on the live web.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_store",
        "description": "Persist a user preference, personal fact, or project rule in JARVIS long-term memory.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "content": {"type": "STRING", "description": "The memory content or fact to store."}
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_search",
        "description": "Search JARVIS long-term memory for relevant stored facts or preferences.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Search query keywords."}},
            "required": ["query"],
        },
    },
]

TOOL_TO_CAPABILITY_MAP: Dict[str, str] = {
    "shell_execute": CAPABILITY_SHELL_EXECUTE,
    "system_info": CAPABILITY_SYSTEM_INFO,
    "system_volume_get": CAPABILITY_SYSTEM_VOLUME,
    "system_volume_set": CAPABILITY_SYSTEM_VOLUME,
    "system_screenshot": CAPABILITY_COMPUTER_SCREENSHOT,
    "filesystem_read": CAPABILITY_FILESYSTEM_READ,
    "filesystem_write": CAPABILITY_FILESYSTEM_WRITE,
    "filesystem_list": CAPABILITY_FILESYSTEM_LIST,
    "filesystem_search": CAPABILITY_FILESYSTEM_SEARCH,
    "process_list": CAPABILITY_PROCESS_ENUMERATE,
    "browser_navigate": CAPABILITY_BROWSER_NAVIGATE,
    "browser_snapshot": CAPABILITY_BROWSER_SNAPSHOT,
    "browser_click": CAPABILITY_BROWSER_CLICK,
    "browser_type": CAPABILITY_BROWSER_TYPE,
    "browser_screenshot": CAPABILITY_BROWSER_SCREENSHOT,
    "app_launch": CAPABILITY_APP_LAUNCH,
    "app_focus": CAPABILITY_WINDOW_FOCUS,
    "app_close": CAPABILITY_WINDOW_CLOSE,
    "window_list": CAPABILITY_WINDOW_LIST,
    "ui_inspect": CAPABILITY_UI_INSPECT,
    "ui_interact": CAPABILITY_UI_INTERACT,
    "whatsapp_call": CAPABILITY_WHATSAPP_CALL,
    "whatsapp_get_latest_call": CAPABILITY_WHATSAPP_HISTORY,
    "whatsapp_status": CAPABILITY_WHATSAPP_STATUS,
    "whatsapp_login": CAPABILITY_WHATSAPP_LOGIN,
    "whatsapp_logout": CAPABILITY_WHATSAPP_LOGOUT,
}


class TelegramLiveSession:
    """Manages an isolated, persistent Gemini 3.8 Live session for Telegram remote control."""

    def __init__(
        self,
        chat_id: int,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        broker: Optional[ActionBroker] = None,
    ) -> None:
        self.chat_id = chat_id
        self.api_key = api_key or settings.TELEGRAM_GEMINI_API_KEY or settings.GEMINI_API_KEY
        self.model_name = model_name or settings.TELEGRAM_GEMINI_MODEL
        self.broker = broker or action_broker
        self.session_id = f"TG-LIVE-{chat_id}-{uuid4().hex[:6].upper()}"

        self._resumption_handle: Optional[str] = None
        self._client: Optional[genai.Client] = None
        self._session_ctx: Any = None
        self._active_session: Any = None
        self._connected = False
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket session is active."""
        return self._connected and self._active_session is not None

    def _build_tools(self) -> List[types.Tool]:
        """Convert tool specifications into Google GenAI types.Tool structures."""
        func_decls: List[types.FunctionDeclaration] = []
        for spec in TELEGRAM_LIVE_TOOLS_SPEC:
            func_decls.append(
                types.FunctionDeclaration(
                    name=spec["name"],
                    description=spec["description"],
                    parameters=spec.get("parameters"),
                )
            )
        return [types.Tool(function_declarations=func_decls)]

    def _build_system_instruction(self) -> str:
        """Construct the authoritative system instruction for Telegram JARVIS."""
        callsign = getattr(settings, "USER_CALLSIGN", "Sir")
        custom_inst = getattr(settings, "TELEGRAM_SYSTEM_INSTRUCTION", None)
        if custom_inst:
            return str(custom_inst)

        return (
            f"You are JARVIS (version 1.0.0), communicating remotely via Telegram with {callsign}. "
            f"Always address the user politely and respectfully as {callsign}. "
            "You are razor-sharp, proactive, helpful, and concise. "
            "You have direct control of the user's host Windows PC through function calling. "
            "When the user asks to find, search for, or check files (e.g. in Downloads or Documents), "
            "use filesystem_search or filesystem_list immediately. "
            "When the user asks to see what is on screen or take a screenshot, use system_screenshot. "
            "When the user asks to place a WhatsApp phone call, deliver a message, or call a contact, "
            "use whatsapp_call(target=..., objective=...). "
            "whatsapp_call waits until the call concludes and returns the recipient's reply—report it immediately. "
            "When the user asks for real-time web information, news, or weather, use web_search. "
            "Respond naturally, cleanly, and concisely in text. Do not emit markdown formatting that breaks display."
        )

    def _build_config(self) -> types.LiveConnectConfig:
        """Build LiveConnectConfig for silent Telegram remote control."""
        instruction = self._build_system_instruction()
        resumption_config = (
            types.SessionResumptionConfig(handle=self._resumption_handle)
            if self._resumption_handle
            else None
        )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=settings.VOICE_DEFAULT_NAME
                    )
                )
            ),
            system_instruction=types.Content(parts=[types.Part.from_text(text=instruction)]),
            tools=self._build_tools(),
            session_resumption=resumption_config,
        )

    async def connect(self) -> None:
        """Establish or resume dedicated Gemini 3.8 Live session."""
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY or TELEGRAM_GEMINI_API_KEY is required.")

        # Ensure all node capability handlers are registered
        from jarvis.execution.browser.host import browser_node
        from jarvis.execution.whatsapp.node import whatsapp_node
        from jarvis.execution.windows.host import windows_node

        windows_node.register_capabilities()
        browser_node.register_capabilities()
        whatsapp_node.register_capabilities()

        # In Telegram live sessions, bypass interactive pauses for read-only / low-risk operations
        if hasattr(self.broker, "policy"):
            self.broker.policy.require_approvals = False

        self._client = genai.Client(api_key=self.api_key)
        config = self._build_config()

        logger.info(
            f"Establishing dedicated Telegram Gemini Live session [{self.session_id}] model={self.model_name}"
        )
        self._session_ctx = self._client.aio.live.connect(model=self.model_name, config=config)
        self._active_session = await self._session_ctx.__aenter__()
        self._connected = True
        logger.info(f"Telegram Gemini Live session [{self.session_id}] connected.")

    async def close(self) -> None:
        """Safely terminate the WebSocket session."""
        self._connected = False
        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception as err:
                logger.debug(f"Telegram live session cleanup: {err}")
            self._session_ctx = None
            self._active_session = None

    async def _execute_tool(
        self, func_name: str, args: Dict[str, Any], call_id: str
    ) -> Tuple[Dict[str, Any], Optional[Path]]:
        """Dispatch a tool call to the authoritative JARVIS execution fabric."""
        logger.info(f"Telegram Live tool execution: {func_name} [{call_id}] args={args}")
        artifact_path: Optional[Path] = None

        # 1. Memory plane store
        if func_name == "memory_store":
            content = str(args.get("content", ""))
            key = str(args.get("key", f"tg-mem-{uuid4().hex[:6]}"))
            record = MemoryRecord(
                key=key,
                content=content,
                memory_type=MemoryType.USER_PREFERENCE,
                session_id=self.session_id,
            )
            stored = memory_manager.store(record)
            return {
                "status": "success",
                "record_id": stored.record_id,
                "key": stored.key,
                "stored": True,
            }, None

        # 2. Memory plane search
        if func_name == "memory_search":
            query = str(args.get("query", ""))
            records = memory_manager.search(query=query, limit=5)
            results_data = [
                {"key": r.key, "content": r.content, "score": round(r.importance_score, 3)}
                for r in records
            ]
            return {
                "status": "success",
                "count": len(results_data),
                "results": results_data,
            }, None

        # 3. Web search
        if func_name == "web_search":
            query = str(args.get("query", "")).strip()
            results = await search_web(query=query)
            return {"status": "success", "query": query, "results": results}, None

        # 4. Host, Browser, and WhatsApp actions through ActionBroker
        capability = TOOL_TO_CAPABILITY_MAP.get(func_name)
        if not capability:
            return {"status": "error", "error": f"Unknown capability for tool: {func_name}"}, None

        if capability in (
            CAPABILITY_BROWSER_NAVIGATE,
            CAPABILITY_BROWSER_SNAPSHOT,
            CAPABILITY_BROWSER_CLICK,
            CAPABILITY_BROWSER_TYPE,
            CAPABILITY_BROWSER_SCREENSHOT,
        ):
            target = ExecutionTarget.BROWSER_NODE
        elif capability in (
            CAPABILITY_WHATSAPP_CALL,
            CAPABILITY_WHATSAPP_STATUS,
            CAPABILITY_WHATSAPP_HISTORY,
            CAPABILITY_WHATSAPP_LOGIN,
            CAPABILITY_WHATSAPP_LOGOUT,
        ):
            target = ExecutionTarget.WHATSAPP_NODE
        else:
            target = ExecutionTarget.WINDOWS_NODE

        action_req = ActionRequest(
            task_id=f"TG-LIVE-{call_id}",
            session_id=self.session_id,
            capability=capability,
            arguments=args,
            target=target,
        )

        result = await self.broker.execute(action_req)

        # Check if output contains an artifact (e.g. screenshot image)
        if isinstance(result.output, dict):
            for candidate_key in ("artifact_path", "filepath", "screenshot_path"):
                p_str = result.output.get(candidate_key)
                if p_str and Path(p_str).exists():
                    artifact_path = Path(p_str)
                    break

        if result.error:
            return {
                "status": "error",
                "action_status": result.status.value,
                "error": result.error,
            }, artifact_path

        return {
            "status": "success",
            "action_status": result.status.value,
            "output": result.output,
            "verified": result.verified,
        }, artifact_path

    async def process_user_turn(
        self, user_text: str, timeout: float = 60.0
    ) -> TelegramLiveTurnResult:
        """Process an inbound Telegram user turn through the dedicated Live session."""
        async with self._lock:
            turn_result = TelegramLiveTurnResult()

            # Ensure active connection
            if not self.is_connected:
                try:
                    await self.connect()
                except Exception as conn_err:
                    logger.error(f"Failed to connect Telegram Live session: {conn_err}")
                    turn_result.error = f"Connection failed: {conn_err}"
                    return turn_result

            try:
                # Transmit user text turn
                content = types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_text)],
                )
                await self._active_session.send_client_content(
                    turns=[content],
                    turn_complete=True,
                )

                turn_finished = False
                in_flight_tool = False
                collected_text: List[str] = []
                max_iterations = 12
                iteration = 0

                while not turn_finished and iteration < max_iterations:
                    iteration += 1
                    async for chunk in self._active_session.receive():
                        # Capture session resumption updates
                        if (
                            chunk.session_resumption_update
                            and chunk.session_resumption_update.new_handle
                        ):
                            self._resumption_handle = chunk.session_resumption_update.new_handle

                        # Process tool calls
                        if chunk.tool_call:
                            in_flight_tool = True
                            function_responses: List[types.FunctionResponse] = []
                            for call in chunk.tool_call.function_calls:
                                call_id = call.id
                                func_name = call.name
                                args = dict(call.args) if call.args else {}
                                turn_result.tools_called.append(func_name)

                                payload, artifact = await self._execute_tool(
                                    func_name=func_name, args=args, call_id=call_id
                                )
                                if artifact:
                                    turn_result.artifacts.append(artifact)

                                function_responses.append(
                                    types.FunctionResponse(
                                        id=call_id,
                                        name=func_name,
                                        response=payload,
                                    )
                                )

                            if function_responses:
                                await self._active_session.send_tool_response(
                                    function_responses=function_responses
                                )

                        # Process server conversational content
                        if chunk.server_content:
                            sc = chunk.server_content
                            if sc.output_transcription and sc.output_transcription.text:
                                collected_text.append(sc.output_transcription.text)
                            if sc.model_turn:
                                for part in sc.model_turn.parts:
                                    if part.text:
                                        collected_text.append(part.text)

                            if sc.turn_complete:
                                if in_flight_tool:
                                    # Continue receiving post-tool response
                                    in_flight_tool = False
                                else:
                                    turn_finished = True
                                    break

                turn_result.text = "".join(collected_text).strip()
                return turn_result

            except Exception as turn_err:
                logger.error(f"Error in Telegram Live session processing: {turn_err}")
                await self.close()
                turn_result.error = str(turn_err)
                return turn_result


class TelegramLiveSessionManager:
    """Manages dedicated per-chat Telegram Gemini 3.8 Live sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[int, TelegramLiveSession] = {}
        self._lock = asyncio.Lock()

    def get_or_create_session(self, chat_id: int) -> TelegramLiveSession:
        """Retrieve existing Live session or initialize a dedicated instance."""
        if chat_id not in self._sessions:
            self._sessions[chat_id] = TelegramLiveSession(chat_id=chat_id)
        return self._sessions[chat_id]

    async def process_message(
        self, chat_id: int, user_text: str, timeout: float = 60.0
    ) -> TelegramLiveTurnResult:
        """Process incoming Telegram user message through the dedicated Gemini Live session."""
        session = self.get_or_create_session(chat_id)
        return await session.process_user_turn(user_text=user_text, timeout=timeout)

    async def close_all(self) -> None:
        """Close all active Telegram Live sessions gracefully."""
        async with self._lock:
            for _chat_id, session in list(self._sessions.items()):
                await session.close()
            self._sessions.clear()


# Global singleton instance
telegram_live_manager = TelegramLiveSessionManager()
