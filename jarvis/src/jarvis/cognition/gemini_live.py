"""Gemini 3.8 Live Conversational Interface & Session Bridge.

Enforces Section 8 and Section 58.1 of ARCHITECTURE.md:
Maintains bidirectional streaming multimodal session with Gemini 3.8 Live,
manages session resumption, and bridges model tool calls directly into the
authoritative ActionBroker and PolicyEngine pipeline.
"""

import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional
from uuid import uuid4

from google import genai
from google.genai import types

from jarvis.actions.broker import ActionBroker, action_broker
from jarvis.config import settings
from jarvis.contracts.action import ActionRequest, ExecutionTarget
from jarvis.policy.firewall import (
    CAPABILITY_COMPUTER_SCREENSHOT,
    CAPABILITY_FILESYSTEM_LIST,
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_FILESYSTEM_WRITE,
    CAPABILITY_PROCESS_ENUMERATE,
    CAPABILITY_SHELL_EXECUTE,
    CAPABILITY_SYSTEM_INFO,
    CAPABILITY_SYSTEM_VOLUME,
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

        # Callbacks for received events
        self.audio_chunk_handler: Optional[Callable[[bytes], Coroutine[Any, Any, None]]] = None
        self.text_chunk_handler: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None
        self.turn_complete_handler: Optional[Callable[[], Coroutine[Any, Any, None]]] = None

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
        system_instruction = (
            "You are JARVIS, an advanced personal AI operating system created for the operator. "
            "You are polite, concise, razor-sharp, and proactive. "
            "You execute tasks on the host system using available tools. "
            "Never refer to third-party project origins. Respond directly and efficiently."
        )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
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
        if self.state != LiveSessionState.ACTIVE or not self._active_session:
            raise RuntimeError(f"Cannot send audio: session is in {self.state.value} state")

        await self._active_session.send_realtime_input(
            media_chunks=[
                types.Blob(
                    data=pcm_bytes,
                    mime_type="audio/pcm;rate=16000",
                )
            ]
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
                            await self._handle_tool_call(response.tool_call)

                        # 3. Process server output content (audio/text)
                        if getattr(response, "server_content", None) and response.server_content:
                            sc = response.server_content
                            if sc.model_turn:
                                for part in sc.model_turn.parts:
                                    if part.inline_data and part.inline_data.data:
                                        await self.audio_output_queue.put(part.inline_data.data)
                                        if self.audio_chunk_handler:
                                            await self.audio_chunk_handler(part.inline_data.data)
                                    if part.text:
                                        if self.text_chunk_handler:
                                            await self.text_chunk_handler(part.text)

                            if sc.turn_complete:
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
        """Execute tool call through authoritative ActionBroker and return response."""
        function_responses: List[types.FunctionResponse] = []

        for call in tool_call.function_calls:
            call_id = call.id
            func_name = call.name
            args = dict(call.args) if call.args else {}
            logger.info(f"Live tool call received: {func_name} [{call_id}] args={args}")

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

            # Construct ActionRequest to pass through PolicyEngine & ActionBroker
            req = ActionRequest(
                task_id=f"LIVE-{call_id}",
                session_id=self.session_id,
                capability=capability,
                arguments=args,
                target=ExecutionTarget.WINDOWS_NODE,
            )

            result = await self.broker.execute(req)

            if result.error:
                response_payload: Dict[str, Any] = {
                    "status": "error",
                    "action_status": result.status.value,
                    "error": result.error,
                }
            else:
                response_payload = {
                    "status": "success",
                    "action_status": result.status.value,
                    "output": result.output,
                    "verified": result.verified,
                }

            function_responses.append(
                types.FunctionResponse(
                    id=call_id,
                    name=func_name,
                    response=response_payload,
                )
            )

        if function_responses:
            logger.info(f"Sending {len(function_responses)} tool responses back to Gemini Live")
            await self._active_session.send_tool_response(function_responses=function_responses)
