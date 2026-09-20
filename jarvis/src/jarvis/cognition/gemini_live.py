"""Gemini 3.8 Live Conversational Interface & Session Bridge.

Enforces Section 8 and Section 58.1 of ARCHITECTURE.md:
Maintains bidirectional streaming multimodal session with Gemini 3.8 Live,
manages session resumption, and bridges model tool calls directly into the
authoritative ActionBroker and PolicyEngine pipeline.
"""

import asyncio
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional
from uuid import uuid4

from google import genai
from google.genai import types

from jarvis.actions.broker import ActionBroker, action_broker
from jarvis.cognition.model_router import TaskClass, model_router
from jarvis.cognition.web_search import search_web
from jarvis.config import settings
from jarvis.contracts.action import ActionRequest, ExecutionTarget
from jarvis.contracts.memory import MemoryRecord, MemoryType
from jarvis.contracts.model import ModelFamily, ModelInvocationRequest
from jarvis.memory.manager import memory_manager
from jarvis.policy.firewall import (
    CAPABILITY_APP_LAUNCH,
    CAPABILITY_BROWSER_CLICK,
    CAPABILITY_BROWSER_NAVIGATE,
    CAPABILITY_BROWSER_SCREENSHOT,
    CAPABILITY_BROWSER_SNAPSHOT,
    CAPABILITY_BROWSER_TYPE,
    CAPABILITY_COMPUTER_ACT,
    CAPABILITY_COMPUTER_SCREENSHOT,
    CAPABILITY_FILESYSTEM_LIST,
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_FILESYSTEM_WRITE,
    CAPABILITY_PROCESS_ENUMERATE,
    CAPABILITY_SHELL_EXECUTE,
    CAPABILITY_SYSTEM_INFO,
    CAPABILITY_SYSTEM_VOLUME,
    CAPABILITY_UI_INSPECT,
    CAPABILITY_UI_INTERACT,
    CAPABILITY_WINDOW_CLOSE,
    CAPABILITY_WINDOW_FOCUS,
    CAPABILITY_WINDOW_LIST,
)
from jarvis.telemetry import logger


def utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class LiveSessionState(str, Enum):
    """Authoritative lifecycle states from Section 58.1 of ARCHITECTURE.md."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATED = "AUTHENTICATED"
    READY = "READY"
    ACTIVE = "ACTIVE"
    RECONNECTING = "RECONNECTING"
    RESUMING = "RESUMING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


# Function declaration mappings for Gemini Live tools
DEFAULT_LIVE_TOOLS: List[Dict[str, Any]] = [
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
        "name": "system_info",
        "description": "Get current host system hardware and operating system status metrics.",
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
        "name": "system_screenshot",
        "description": "Capture a screenshot of the current primary Windows desktop display.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "filesystem_read",
        "description": "Read text contents of a file on the local filesystem.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING", "description": "Path to the file to read."}},
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
        "name": "filesystem_list",
        "description": "List files and subdirectories in a directory path.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING", "description": "Directory path to list."}},
            "required": ["path"],
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
        "name": "delegate_task",
        "description": "Assign a complex reasoning, deep coding, research, or analytical task to a specialist worker model (such as GPT-OSS 120B or Qwen 3.8 27B) and return the completed result.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instruction": {
                    "type": "STRING",
                    "description": "The detailed instructions and requirements for the specialist model.",
                },
                "target_model": {
                    "type": "STRING",
                    "description": "Target specialist model: 'GPT-OSS 120B', 'Qwen 3.8 27B', or 'Gemini 3.5 Flash-Lite'.",
                },
                "task_type": {
                    "type": "STRING",
                    "description": "Category of work: 'CODING', 'DEEP_REASONING', or 'RESEARCH'.",
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "browser_navigate",
        "description": "Navigate to a URL using the autonomous browser to inspect contents and capture screenshots.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING", "description": "The URL to navigate to."}},
            "required": ["url"],
        },
    },
    {
        "name": "browser_snapshot",
        "description": "Capture structured text, links, and accessibility tree of the current webpage to inspect elements and content.",
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
        "description": "Click an element on the current webpage using a CSS selector, link text, or button selector (e.g. 'text=Search', 'button#submit', 'a[href*=watch]').",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "selector": {
                    "type": "STRING",
                    "description": "CSS selector, text selector (e.g. 'text=Play', 'button.yt-spec-button-shape-next'), or XPath.",
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
                    "description": "CSS selector for the input field (e.g. 'input[name=search_query]', 'input#search').",
                },
                "text": {
                    "type": "STRING",
                    "description": "The text string to type into the field.",
                },
                "press_enter": {
                    "type": "BOOLEAN",
                    "description": "Optional: whether to press Enter after typing to submit form or search (default: true).",
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
        "name": "app_launch",
        "description": "Launch an installed Windows application or registered protocol handler asynchronously without blocking.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {
                    "type": "STRING",
                    "description": "Application name (e.g. 'Notepad', 'Paint', 'Spotify', 'Chrome') or executable path.",
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
        "description": "Enumerate all open desktop application windows with titles, HWNDs, and process information.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "ui_inspect",
        "description": "Inspect the live interactive UI automation tree of an application window to discover controls (buttons, tabs, inputs, menus, sliders).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "window_target": {
                    "type": "STRING",
                    "description": "Optional window title or application name. If omitted, inspects the current foreground window.",
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
        "description": "Interact with an in-app UI control using Microsoft UI Automation patterns (click, set_value, toggle, select, expand, collapse, scroll) with closed-loop verification.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "window_target": {
                    "type": "STRING",
                    "description": "Window title or application name containing the control.",
                },
                "element_query": {
                    "type": "STRING",
                    "description": "Control name, label, or AutomationId (e.g. 'File', 'Save', 'Search', 'Text').",
                },
                "action": {
                    "type": "STRING",
                    "description": "Action to perform: 'click', 'set_value', 'toggle', 'select', 'expand', 'collapse', 'scroll' (default: 'click').",
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
        "name": "computer_action",
        "description": "Execute a canonical computer-use action (mouse click, type, key, screenshot, wait) as a fallback.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action name: 'screenshot', 'left_click', 'right_click', 'double_click', 'type', 'key', 'wait'.",
                },
                "x": {"type": "NUMBER", "description": "X coordinate for mouse clicks."},
                "y": {"type": "NUMBER", "description": "Y coordinate for mouse clicks."},
                "text": {"type": "STRING", "description": "Text to type."},
                "keys": {
                    "type": "STRING",
                    "description": "Keyboard key to press (e.g. 'enter', 'tab', 'esc').",
                },
            },
            "required": ["action"],
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
    "computer_action": CAPABILITY_COMPUTER_ACT,
}


class GeminiLiveBridge:
    """Manages full lifecycle of Gemini 3.8 Live bidirectional sessions."""

    def __init__(
        self,
        session_id: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        voice_name: Optional[str] = None,
        broker: Optional[ActionBroker] = None,
    ) -> None:
        self.session_id = session_id or f"LIVE-{uuid4().hex[:8].upper()}"
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model_name = model_name or settings.MODEL_MAP_GEMINI_LIVE
        self.voice_name = voice_name or settings.VOICE_DEFAULT_NAME
        self.broker = broker or action_broker

        self.state = LiveSessionState.DISCONNECTED
        self.resumption_handle: Optional[str] = None
        self._client: Optional[genai.Client] = None
        self._session_ctx: Any = None
        self._active_session: Any = None
        self._receive_task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self.in_flight_tool_call: bool = False

        # Callbacks for received events
        self.audio_chunk_handler: Optional[Callable[[bytes], Coroutine[Any, Any, None]]] = None
        self.text_chunk_handler: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None
        self.turn_complete_handler: Optional[Callable[[], Coroutine[Any, Any, None]]] = None
        self.input_transcription_handler: Optional[
            Callable[[str, bool], Coroutine[Any, Any, None]]
        ] = None
        self.interrupted_handler: Optional[Callable[[], Coroutine[Any, Any, None]]] = None
        self.tool_call_handler: Optional[
            Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]
        ] = None

        # Internal queues
        self.audio_output_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _build_tools(self) -> List[types.Tool]:
        """Convert default live tool declarations to GenAI Tool objects."""
        func_decls: List[types.FunctionDeclaration] = []
        for tool_spec in DEFAULT_LIVE_TOOLS:
            func_decls.append(
                types.FunctionDeclaration(
                    name=tool_spec["name"],
                    description=tool_spec["description"],
                    parameters=tool_spec.get("parameters"),
                )
            )
        return [types.Tool(function_declarations=func_decls)]

    def _build_config(self) -> types.LiveConnectConfig:
        """Construct the authoritative LiveConnectConfig."""
        callsign = getattr(settings, "USER_CALLSIGN", "Sir")
        system_instruction = (
            "You are JARVIS (version 3.0.0), the personal AI operating system and master orchestrator. "
            f"Always address the user with respect as {callsign} (or by their preferred honorific). "
            "You are razor-sharp, proactive, polite, and concise. "
            "You control the host system, the live web, and all specialist worker models without requiring approval tickets. "
            "When the user requests real-time information, news, weather, or web searches, use web_search. "
            "When the user asks to open Chrome and browse, search, or play media (e.g. YouTube, web pages), "
            "immediately use browser_navigate (e.g. browser_navigate('https://www.youtube.com')). "
            "browser_navigate automatically opens and controls Chrome visibly on the desktop. "
            "Do NOT call app_launch('chrome.exe') separately when performing web automation—browser_navigate directly launches and controls the visible browser. "
            "To search inside a webpage, use browser_type(selector='input[name=search_query]', text='...', press_enter=True). "
            "To select and play a video or click a link, use browser_click(selector='a#video-title') or browser_snapshot to extract available elements. "
            "For native desktop apps (Notepad, Calculator, Paint), use app_launch, ui_inspect, ui_interact, or computer_action. "
            "When the user requests deep coding or heavy reasoning, assign the task to specialist models using delegate_task. "
            f"Respond directly, concisely, and efficiently, always addressing the user as {callsign}."
        )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice_name)
                )
            ),
            system_instruction=types.Content(parts=[types.Part.from_text(text=system_instruction)]),
            tools=self._build_tools(),
        )

    async def connect(self) -> None:
        """Establish bidirectional streaming connection with Gemini 3.8 Live."""
        if not self.api_key:
            self.state = LiveSessionState.FAILED
            raise ValueError("GEMINI_API_KEY is required to connect to Gemini 3.8 Live.")

        self.state = LiveSessionState.CONNECTING
        logger.info(
            f"Connecting GeminiLiveBridge [{self.session_id}] to {self.model_name} with voice '{self.voice_name}'"
        )

        from jarvis.execution.browser.host import browser_node
        from jarvis.execution.windows.host import windows_node

        windows_node.register_capabilities()
        browser_node.register_capabilities()

        # In interactive live sessions, bypass approval ticket pauses so host operations execute directly
        if hasattr(self.broker, "policy"):
            self.broker.policy.require_approvals = False

        self._client = genai.Client(api_key=self.api_key)
        config = self._build_config()

        self._session_ctx = self._client.aio.live.connect(model=self.model_name, config=config)
        self._active_session = await self._session_ctx.__aenter__()

        self.state = LiveSessionState.ACTIVE
        self._stop_event.clear()
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info(f"GeminiLiveBridge [{self.session_id}] ACTIVE and streaming.")

    async def disconnect(self) -> None:
        """Gracefully close the Live streaming connection."""
        self._stop_event.set()
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if hasattr(self.broker, "policy"):
            self.broker.policy.require_approvals = settings.REQUIRE_APPROVALS

        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing Live session context: {e}")

        self.state = LiveSessionState.CLOSED
        logger.info(f"GeminiLiveBridge [{self.session_id}] CLOSED.")

    async def send_text(self, text: str) -> None:
        """Send a user text prompt into the active Live session."""
        if self.state != LiveSessionState.ACTIVE or not self._active_session:
            raise RuntimeError(f"Cannot send text: session is in {self.state.value} state")

        logger.info(f"Sending client text to Gemini Live: {text[:60]}...")
        await self._active_session.send_client_content(
            turns=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=text)],
                )
            ],
            turn_complete=True,
        )

    async def send_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Send a chunk of PCM audio input to the active Live session."""
        if (
            self._stop_event.is_set()
            or self.state != LiveSessionState.ACTIVE
            or not self._active_session
        ):
            return

        try:
            await self._active_session.send_realtime_input(
                audio=types.Blob(
                    data=pcm_bytes,
                    mime_type="audio/pcm;rate=16000",
                )
            )
        except Exception as err:
            if not self._stop_event.is_set():
                logger.debug(f"Audio chunk dispatch error: {err}")

    async def send_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        prompt: Optional[str] = None,
    ) -> None:
        """Send an image to the active Live session with an optional user prompt."""
        if self.state != LiveSessionState.ACTIVE or not self._active_session:
            raise RuntimeError(f"Cannot send image: session is in {self.state.value} state")

        logger.info(f"Sending image ({len(image_bytes)} bytes, {mime_type}) to Gemini Live...")
        if prompt:
            await self._active_session.send_client_content(
                turns=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                            types.Part.from_text(text=prompt),
                        ],
                    )
                ],
                turn_complete=True,
            )
        else:
            await self._active_session.send_realtime_input(
                media=types.Blob(
                    data=image_bytes,
                    mime_type=mime_type,
                )
            )

    async def send_video_frame(
        self,
        frame_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> None:
        """Stream a real-time video frame to the active Live session."""
        if self.state != LiveSessionState.ACTIVE or not self._active_session:
            raise RuntimeError(f"Cannot send video frame: session is in {self.state.value} state")

        await self._active_session.send_realtime_input(
            video=types.Blob(
                data=frame_bytes,
                mime_type=mime_type,
            )
        )

    async def _receive_loop(self) -> None:
        """Continuous background event loop receiving live server events across multi-turn sessions."""
        try:
            while not self._stop_event.is_set():
                try:
                    async for response in self._active_session.receive():
                        if self._stop_event.is_set():
                            break

                        # 1. Update session resumption handle if provided
                        if getattr(response, "session_resumption_update", None):
                            update = response.session_resumption_update
                            if hasattr(update, "new_session_handle"):
                                self.resumption_handle = update.new_session_handle
                                logger.info(
                                    f"Updated live session resumption handle: {self.resumption_handle}"
                                )

                        # 2. Intercept and dispatch tool calls
                        if getattr(response, "tool_call", None) and response.tool_call:
                            self.in_flight_tool_call = True
                            await self._handle_tool_call(response.tool_call)

                        # 3. Process server output content (audio/text/transcription/interruption)
                        if getattr(response, "server_content", None) and response.server_content:
                            sc = response.server_content

                            # Handle server-detected interruption (barge-in)
                            if getattr(sc, "interrupted", False):
                                logger.info("Live session: user barge-in detected by server")
                                self.in_flight_tool_call = False
                                while not self.audio_output_queue.empty():
                                    try:
                                        self.audio_output_queue.get_nowait()
                                    except Exception:
                                        break
                                if self.interrupted_handler:
                                    await self.interrupted_handler()

                            # Handle user input audio transcription
                            if (
                                getattr(sc, "input_transcription", None)
                                and sc.input_transcription.text
                            ):
                                in_text = sc.input_transcription.text
                                in_finished = getattr(sc.input_transcription, "finished", False)
                                if self.input_transcription_handler:
                                    await self.input_transcription_handler(in_text, in_finished)

                            if sc.model_turn:
                                for part in sc.model_turn.parts:
                                    if part.inline_data and part.inline_data.data:
                                        self.in_flight_tool_call = False
                                        await self.audio_output_queue.put(part.inline_data.data)
                                        if self.audio_chunk_handler:
                                            await self.audio_chunk_handler(part.inline_data.data)
                                    if part.text:
                                        self.in_flight_tool_call = False
                                        if self.text_chunk_handler:
                                            await self.text_chunk_handler(part.text)

                            if sc.output_transcription and sc.output_transcription.text:
                                self.in_flight_tool_call = False
                                if self.text_chunk_handler:
                                    await self.text_chunk_handler(sc.output_transcription.text)

                            if sc.turn_complete and not self.in_flight_tool_call:
                                if self.turn_complete_handler:
                                    await self.turn_complete_handler()
                                break
                except asyncio.CancelledError:
                    break
                except Exception as loop_err:
                    if self._stop_event.is_set():
                        break
                    logger.debug(f"Live turn stream cycle: {loop_err}")
                    await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            pass
        except Exception as err:
            logger.error(f"Error in GeminiLiveBridge receive loop: {err}", exc_info=True)
            self.state = LiveSessionState.FAILED

    async def _handle_tool_call(self, tool_call: Any) -> None:
        """Execute tool call through ActionBroker, ModelRouter, or MemoryPlane and return response."""
        function_responses: List[types.FunctionResponse] = []

        for call in tool_call.function_calls:
            call_id = call.id
            func_name = call.name
            args = dict(call.args) if call.args else {}
            logger.info(f"Live tool call received: {func_name} [{call_id}] args={args}")

            if self.tool_call_handler:
                try:
                    await self.tool_call_handler(func_name, args)
                except Exception as cb_err:
                    logger.debug(f"Error in tool_call_handler callback: {cb_err}")

            # 1. Delegation to specialist models (GPT-OSS 120B, Qwen 3.8 27B, etc.)
            if func_name == "delegate_task":
                instruction = str(args.get("instruction", ""))
                target_str = str(args.get("target_model", "")).lower()
                task_type_str = str(args.get("task_type", "CODING")).upper()

                if "reason" in task_type_str:
                    task_class = TaskClass.DEEP_REASONING
                elif "research" in task_type_str:
                    task_class = TaskClass.EXTRACTION_SUMMARIZATION
                else:
                    task_class = TaskClass.CODING

                if "qwen" in target_str or "27b" in target_str:
                    target_family = ModelFamily.QWEN_3_8_27B
                elif "3.5" in target_str or "flash-lite" in target_str:
                    target_family = ModelFamily.GEMINI_3_5_FLASH_LITE
                elif "gemma" in target_str or "31b" in target_str:
                    target_family = ModelFamily.GEMMA_4_31B
                else:
                    target_family = ModelFamily.GPT_OSS_120B

                logger.info(
                    f"Delegating task to specialist model {target_family.value} [{task_class.value}]: {instruction[:60]}..."
                )
                model_req = ModelInvocationRequest(
                    model_family=target_family,
                    prompt=instruction,
                    system_instruction=(
                        "You are an expert specialist model running under JARVIS master orchestration. "
                        "Complete the requested task with maximum depth, accuracy, and technical excellence."
                    ),
                    max_output_tokens=2048,
                )
                model_res = await model_router.invoke(request=model_req, task_class=task_class)

                delegation_payload: Dict[str, Any]
                if model_res.error:
                    delegation_payload = {
                        "status": "error",
                        "delegated_model": model_res.model_family.value,
                        "error": model_res.error,
                    }
                else:
                    delegation_payload = {
                        "status": "success",
                        "delegated_model": model_res.model_family.value,
                        "result": model_res.text_content,
                        "tokens_used": model_res.total_tokens,
                    }

                function_responses.append(
                    types.FunctionResponse(
                        id=call_id,
                        name=func_name,
                        response=delegation_payload,
                    )
                )
                continue

            # 2. Memory plane store
            if func_name == "memory_store":
                content = str(args.get("content", ""))
                key = str(args.get("key", f"pref-{uuid4().hex[:6]}"))
                record = MemoryRecord(
                    key=key,
                    content=content,
                    memory_type=MemoryType.USER_PREFERENCE,
                    session_id=self.session_id,
                )
                stored = memory_manager.store(record)
                function_responses.append(
                    types.FunctionResponse(
                        id=call_id,
                        name=func_name,
                        response={
                            "status": "success",
                            "record_id": stored.record_id,
                            "key": stored.key,
                            "stored": True,
                        },
                    )
                )
                continue

            # 3. Memory plane search
            if func_name == "memory_search":
                query = str(args.get("query", ""))
                records = memory_manager.search(query=query, limit=5)
                results_data = [
                    {"key": r.key, "content": r.content, "score": round(r.importance_score, 3)}
                    for r in records
                ]
                function_responses.append(
                    types.FunctionResponse(
                        id=call_id,
                        name=func_name,
                        response={
                            "status": "success",
                            "count": len(results_data),
                            "results": results_data,
                        },
                    )
                )
                continue

            # 4. Web search for real-time web lookups
            if func_name == "web_search":
                query = str(args.get("query", "")).strip()
                logger.info(f"Live web_search requested for query: '{query}'")
                search_results = await search_web(query=query)
                function_responses.append(
                    types.FunctionResponse(
                        id=call_id,
                        name=func_name,
                        response={
                            "status": "success",
                            "query": query,
                            "count": len(search_results),
                            "results": search_results,
                        },
                    )
                )
                continue

            # 5. Host and Browser Action Execution through ActionBroker
            capability = TOOL_TO_CAPABILITY_MAP.get(func_name)
            if not capability:
                error_msg = f"Unknown tool function: {func_name}"
                logger.error(error_msg)
                function_responses.append(
                    types.FunctionResponse(
                        id=call_id,
                        name=func_name,
                        response={"status": "error", "error": error_msg},
                    )
                )
                continue

            target = (
                ExecutionTarget.BROWSER_NODE
                if capability
                in (
                    CAPABILITY_BROWSER_NAVIGATE,
                    CAPABILITY_BROWSER_SNAPSHOT,
                    CAPABILITY_BROWSER_CLICK,
                    CAPABILITY_BROWSER_TYPE,
                    CAPABILITY_BROWSER_SCREENSHOT,
                )
                else ExecutionTarget.WINDOWS_NODE
            )

            action_req = ActionRequest(
                task_id=f"LIVE-{call_id}",
                session_id=self.session_id,
                capability=capability,
                arguments=args,
                target=target,
            )

            result = await self.broker.execute(action_req)

            action_payload: Dict[str, Any]
            if result.error:
                action_payload = {
                    "status": "error",
                    "action_status": result.status.value,
                    "error": result.error,
                }
            else:
                action_payload = {
                    "status": "success",
                    "action_status": result.status.value,
                    "output": result.output,
                    "verified": result.verified,
                }

            # Closed-loop visual feedback: if the tool produced visual screen data,
            # feed the image frame into the active Live session so Gemini is not blind.
            try:
                img_bytes: Optional[bytes] = None
                if isinstance(result.output, dict):
                    if "base64" in result.output and result.output["base64"]:
                        import base64

                        img_bytes = base64.b64decode(result.output["base64"])
                    elif "artifact_path" in result.output and result.output["artifact_path"]:
                        p = Path(result.output["artifact_path"])
                        if p.exists():
                            img_bytes = p.read_bytes()
                    elif "filepath" in result.output and result.output["filepath"]:
                        p = Path(result.output["filepath"])
                        if p.exists():
                            img_bytes = p.read_bytes()

                if img_bytes and self.state == LiveSessionState.ACTIVE and self._active_session:
                    await self.send_image(
                        img_bytes,
                        mime_type="image/jpeg",
                        prompt=f"[Visual context update after {func_name}]",
                    )
                    logger.info(
                        f"Injected visual observation frame ({len(img_bytes)} bytes) into Gemini Live session"
                    )
            except Exception as obs_err:
                logger.debug(f"Visual feedback delivery skipped: {obs_err}")

            function_responses.append(
                types.FunctionResponse(
                    id=call_id,
                    name=func_name,
                    response=action_payload,
                )
            )

        if function_responses:
            logger.info(f"Sending {len(function_responses)} tool responses back to Gemini Live")
            await self._active_session.send_tool_response(function_responses=function_responses)
