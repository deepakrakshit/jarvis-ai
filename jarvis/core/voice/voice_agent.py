"""JARVIS Realtime Voice Agent Coordinator.

Integrates the RealtimeModelAdapter, LiveSessionManager, LiveToolBridge,
and BackgroundTaskManager into a responsive, continuously conversational voice plane.
Guarantees:
- Realtime voice dialogue while background autonomous tasks execute.
- Truthful milestone progress notifications (zero fake progress, anti-spam throttling).
- True user interruption / barge-in.
- Flash Pool protection (ordinary voice never consumes scarce Flash RPD).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum

from jarvis.core.config import get_settings
from jarvis.core.gateway.realtime import (
    LiveAudioChunk,
    LiveEvent,
    LiveEventType,
    LiveSessionConfig,
    LiveToolCall,
    LiveToolResponse,
    LiveTranscription,
    RealtimeModelAdapter,
)
from jarvis.core.logging import get_logger
from jarvis.core.voice.session_manager import LiveSessionManager
from jarvis.core.voice.task_manager import BackgroundTaskManager, VoiceTaskEvent
from jarvis.core.voice.telemetry import VoiceTelemetryLogger
from jarvis.core.voice.tool_bridge import LiveToolBridge

logger = get_logger(__name__)


class MicrophoneMode(StrEnum):
    """Operational mode governing when client microphone audio is streamed."""

    PUSH_TO_TALK = "PUSH_TO_TALK"
    WAKE_WORD = "WAKE_WORD"
    ACTIVE_CONVERSATION = "ACTIVE_CONVERSATION"


class LiveVoiceAgent:
    """The Realtime Voice Plane frontend coordinator for JARVIS."""

    def __init__(
        self,
        session_id: str,
        adapter: RealtimeModelAdapter,
        task_manager: BackgroundTaskManager | None = None,
        tool_bridge: LiveToolBridge | None = None,
        model_id: str | None = None,
        mic_mode: MicrophoneMode = MicrophoneMode.ACTIVE_CONVERSATION,
    ) -> None:
        self.session_id = session_id
        self.adapter = adapter
        self.model_id = model_id or get_settings().REALTIME_VOICE_MODEL_ID
        self.mic_mode = mic_mode

        self.telemetry = VoiceTelemetryLogger(session_id=session_id)
        self.session_manager = LiveSessionManager(adapter=adapter, telemetry=self.telemetry)
        self.task_manager = task_manager or BackgroundTaskManager()
        self.tool_bridge = tool_bridge or LiveToolBridge(task_manager=self.task_manager)

        # Event distribution to external consumers (e.g. UI, speaker driver)
        self._outbound_events: asyncio.Queue[LiveEvent] = asyncio.Queue()
        self._receive_task: asyncio.Task[None] | None = None
        self._is_running = False

        # Track active tool call execution to prevent interleaved text turns
        self._active_tool_call: bool = False
        self.current_user_intent: str | None = None

        # Anti-spam milestone throttling (seconds between proactive progress broadcasts)
        self.last_proactive_broadcast: datetime = datetime.min.replace(tzinfo=UTC)
        self.proactive_throttle_seconds: float = 3.0

        # Subscribe to task milestone notifications
        self.task_manager.subscribe(self._on_background_task_event)

    @property
    def voice_name(self) -> str:
        """Return active session voice identity name."""
        if self.session_manager.current_session:
            return self.session_manager.current_session.voice_name
        return get_settings().VOICE_DEFAULT_NAME

    async def start(
        self,
        voice_name: str | None = None,
        system_instruction: str | None = None,
        thinking_budget: int | None = None,
        thinking_level: str | None = None,
    ) -> None:
        """Initialize the voice session and start the asynchronous event listening loop."""
        tools_def = self.tool_bridge.get_tool_definitions()
        # Wire realtime visual perception sink to adapter
        if hasattr(self.adapter, "send_image"):
            self.tool_bridge.set_image_sink(self.adapter.send_image)

        active_voice = voice_name or get_settings().VOICE_DEFAULT_NAME

        config = LiveSessionConfig(
            model_id=self.model_id,
            voice_name=active_voice,
            system_instruction=system_instruction
            or (
                "You are JARVIS, an autonomous AI operating system with realtime voice interaction. "
                "Speak naturally, concisely, and truthfully to the user using your direct voice. "
                "CRITICAL INVARIANTS: "
                "1. Direct & Substantive Answers: When the user asks for information, facts, file contents, or web searches, "
                "provide the core information directly and immediately in the same turn. Do NOT make small-talk promises like "
                "'I am inspecting it now and will let you know' or 'I will get right on that'. Deliver the actual findings at once. "
                "2. Real-World Temporal State: Never guess or hallucinate the date or time. Whenever the user asks for the current time, "
                "date, day of the week, or timestamp, you MUST invoke jarvis_get_time to query the authoritative system clock. "
                "3. File Inspection & Reading: Whenever the user asks to read, inspect, check, or summarize any workspace file or dependencies "
                "(e.g., requirements.txt, pyproject.toml, configs, code files), invoke jarvis_read_file immediately. "
                "Once the file content returns, synthesize and speak the key information (e.g. main libraries, configurations, or findings) at once. "
                "4. Web Search & Intelligence: Whenever the user asks to search the web, check current news, or look up recent models or external topics "
                "(e.g. GPT-6 Astra, recent AI developments, documentation), invoke jarvis_search_web immediately. "
                "Once search results return, deliver a concise summary of the key facts directly to the user at once. "
                "5. Background Autonomous Workloads: When the user asks for a background task, security audit, architecture inspection, or autonomous workload, invoke jarvis_task immediately. Confirm to the user that the background task has been launched in the background and that you remain available for live voice conversation while it runs. "
                "6. Task Status & Phase Queries: When the user asks if you are still working on a task, what phase you are currently in, or asks for progress based only on what is completed, invoke jarvis_get_task_status immediately to query the live task state. Report the authentic phase, status, and progress directly to the user based solely on the returned tool data. "
                "7. Conversational Dialogue & Explanations: For general knowledge or conceptual questions (e.g. explaining TCP vs UDP), answer directly, concisely, and naturally using your voice without interrupting background tasks. Do NOT call jarvis_chat for normal conversational replies; always speak directly to the user. "
                "8. Workspace Inspection & Math Operations: When the user asks to list directory contents or calculate mathematical expressions, invoke jarvis_list_dir or jarvis_calc immediately. When asked to create, write, or append to files, invoke jarvis_write_file immediately in the current turn to route mutations safely through zero-trust governance. If asked for a substantial or detailed test file (e.g. 10-50 KB), generate comprehensive, detailed sections with synthetic logs, component descriptions, and architectural notes to reach the requested size. When asked to delete a file, invoke jarvis_delete_file to route deletions safely through zero-trust governance. "
                "9. Strict User-Intent Adherence: Read means read. Write means write. Append means append now in the current turn. Query means query. Delete means delete. Never perform unrequested mutations. When the user asks to read, inspect, check, or summarize a file, invoke ONLY jarvis_read_file. Under no circumstances should you rewrite, update, expand, or delete a file during a read or query request, even if a previous turn mentioned a desired size or objective. "
                "10. Citation Integrity & Zero-Fabrication: When drafting, summarizing, or writing reports, documents, or bibliographies, you MUST NEVER fabricate, guess, or synthesize publication dates, authors, journal venues, or paper titles. Never 'year-upgrade' historical publications (e.g. projecting 2019/2021 literature to 2026). When asked for citations, references, or an academic report, invoke jarvis_search_web for candidate discovery, and invoke jarvis_verify_citation to retrieve and verify authoritative machine-readable metadata (exact title, verified author names, authentic year, venue, DOI) from scholarly providers before writing. Never fabricate authors or cite literature without verified metadata. "
                "11. Epistemic Calibration & Evidence-Based Claims: In medical, scientific, and technical analysis, maintain strict epistemic calibration. Do NOT make unevidenced absolute superlative claims (such as 'superhuman accuracy', 'higher survival rates', 'well before visible to the human eye', or 'flawless diagnosis'). Instead, frame findings accurately with context: specify whether evidence stems from retrospective benchmark evaluations, controlled validation studies, or prospective clinical trials, and clearly state necessary translational, safety, and regulatory limitations. "
                "12. Documentation-First Engineering & Governed Testing Loop: When asked to research a library, write code, run tests, and revise on failure: "
                "First, invoke jarvis_search_web and jarvis_fetch_web to inspect official documentation before implementing. "
                "Second, invoke jarvis_write_file to create the implementation and test script in the workspace. "
                "Third, ALWAYS invoke jarvis_run_python_test to execute workspace Python scripts or pytest suites. NEVER use jarvis_shell for running Python tests or scripts. "
                "Fourth, if jarvis_run_python_test fails (exit_code != 0), inspect the traceback in stderr/stdout, diagnose the failure, revise the code using jarvis_write_file, and re-run jarvis_run_python_test until verified. Do not claim success until execution actually passes. "
                "Fifth, if any tool is blocked by policy or requires approval, NEVER repeatedly retry the identical blocked tool call. Immediately switch to an authorized alternative tool or inform the user. "
                "13. Read-Only System Queries & Environment Information: When the user asks for system facts, Python version, OS platform, environment info, or directory state, NEVER write, create, or modify files or scripts (such as creating temporary test scripts or version-check scripts). Queries about Python version, environment, or system status must NEVER invoke jarvis_write_file. Use direct voice response, read-only inspection, or safe system queries. "
                "14. Operating System & Desktop Application Control: You have direct native authority over host desktop applications, windows, and display surface. "
                "When the user asks to open, launch, or start an application (e.g. 'open chrome', 'launch notepad', 'open calc', 'open browser'), you MUST immediately invoke jarvis_launch_app(app_name=...). "
                "When the user asks to close, quit, or terminate an application (e.g. 'close chrome', 'exit notepad'), you MUST invoke jarvis_close_app(app_name=...). "
                "When the user asks what applications or processes are running or open, invoke jarvis_list_apps. "
                "When the user asks to take a screenshot or inspect the display, invoke jarvis_screenshot. "
                "When the user asks what window is currently focused or active, invoke jarvis_get_window. "
                "When the user asks to bring an application to the foreground, invoke jarvis_focus_window(window_title=...). "
                "When the user asks to open a website, browse to a URL, or open something in a browser (e.g. 'open youtube in it', 'go to youtube.com'), invoke jarvis_navigate_browser(url=...). "
                "When typing into a specific application, you MUST ensure that application is focused first with jarvis_focus_window. If focusing is denied or fails, do NOT blindly type into the desktop. "
                "When the user asks to click, double-click, scroll, or type into an application or window, invoke jarvis_click, jarvis_scroll, or jarvis_type. "
                "When the user asks to inspect UI elements, buttons, or controls, invoke jarvis_inspect_ui. "
                "NEVER tell the user that you cannot open applications or control programs on their device. You have full native desktop authority."
            ),
            tools=tools_def,
            thinking_budget=thinking_budget,
            thinking_level=thinking_level,
            enable_compression=False,
            input_transcription=True,
            output_transcription=True,
        )

        await self.session_manager.initialize_session(
            jarvis_session_id=self.session_id,
            config=config,
        )

        self._is_running = True
        self._receive_task = asyncio.create_task(
            self._run_receive_loop(),
            name=f"voice_receive_loop_{self.session_id}",
        )
        logger.info("live_voice_agent_started", session_id=self.session_id, model_id=self.model_id)

    async def _run_receive_loop(self) -> None:
        """Continuous event consumption loop handling audio, tools, GoAway, and barge-in."""
        try:
            async for event in self.adapter.receive_events():
                # 1. Process server tool calls through zero-trust governance
                if event.event_type == LiveEventType.TOOL_CALL:
                    self._active_tool_call = True
                    try:
                        raw_tc = event.payload
                        if isinstance(raw_tc, dict):
                            tool_call = LiveToolCall.model_validate(raw_tc)
                        else:
                            tool_call = raw_tc

                        self.telemetry.log_tool_call(
                            tool_call.name, tool_call.call_id, tool_call.is_non_blocking
                        )

                        # If jarvis_chat was called, forward its message as a text delta
                        if tool_call.name == "jarvis_chat" and "message" in tool_call.arguments:
                            await self._outbound_events.put(
                                LiveEvent(
                                    event_type=LiveEventType.TEXT_DELTA,
                                    payload=str(tool_call.arguments["message"]),
                                )
                            )

                        # Forward tool call event to UI queue immediately so terminal renders tool start
                        await self._outbound_events.put(event)

                        # Execute tool call asynchronously through zero-trust governance
                        response = await self.tool_bridge.execute_tool_call(
                            tool_call=tool_call,
                            session_id=self.session_id,
                            user_intent=self.current_user_intent,
                        )
                        self.telemetry.log_tool_response(
                            response.name, response.call_id, response.response.get("status", "ok")
                        )

                        # Forward tool response event to UI queue so terminal renders completion or denial
                        await self._outbound_events.put(
                            LiveEvent(
                                event_type=LiveEventType.TOOL_RESPONSE,
                                payload=response,
                            )
                        )
                        await self.adapter.send_tool_response(response)
                        img_bytes = getattr(self.tool_bridge, "last_captured_image_bytes", None)
                        if img_bytes is not None and hasattr(self.adapter, "send_image"):
                            with suppress(Exception):
                                await self.adapter.send_image(img_bytes)
                            self.tool_bridge.last_captured_image_bytes = None
                    except Exception as tool_exc:
                        raw_tc = event.payload
                        call_name = (
                            getattr(raw_tc, "name", None)
                            or (raw_tc.get("name") if isinstance(raw_tc, dict) else None)
                            or "unknown"
                        )
                        call_id = (
                            getattr(raw_tc, "call_id", None)
                            or (raw_tc.get("call_id") if isinstance(raw_tc, dict) else None)
                            or "unknown"
                        )
                        logger.error(
                            "tool_execution_failed_in_receive_loop",
                            error=str(tool_exc),
                            tool_name=call_name,
                        )
                        fallback_resp = LiveToolResponse(
                            call_id=call_id,
                            name=call_name,
                            response={
                                "status": "error",
                                "error": f"Tool execution failed: {tool_exc}",
                                "reason_code": "EXECUTION_ERROR",
                            },
                            scheduling="WHEN_IDLE",
                        )
                        with suppress(Exception):
                            await self._outbound_events.put(
                                LiveEvent(
                                    event_type=LiveEventType.TOOL_RESPONSE,
                                    payload=fallback_resp,
                                )
                            )
                        with suppress(Exception):
                            await self.adapter.send_tool_response(fallback_resp)
                    finally:
                        self._active_tool_call = False
                    continue

                # 2. Process transparent GoAway connection rotation
                elif event.event_type == LiveEventType.GO_AWAY:
                    await self.session_manager.handle_go_away(event.payload)

                # 3. Process Session Resumption Token updates
                elif event.event_type == LiveEventType.RESUMPTION_UPDATE:
                    self.session_manager.update_resumption_handle(event.payload)

                # 4. Process Client Interruption / Barge-in
                elif event.event_type == LiveEventType.INTERRUPTED:
                    self.telemetry.log_interrupt()

                # 5. Telemetry for audio, text, and user transcription
                elif event.event_type == LiveEventType.TRANSCRIPTION:
                    trans: LiveTranscription = event.payload
                    if trans.is_user and trans.text:
                        self.current_user_intent = trans.text
                        if self._is_approval_text(trans.text):
                            resolved_auth = self.tool_bridge.resolve_pending_approval(approved=True)
                            if resolved_auth:
                                logger.info(
                                    "hitl_approval_resolved_via_user_speech",
                                    proposal_id=str(resolved_auth.proposal_id),
                                    tool_id=resolved_auth.tool_id,
                                )
                        elif self._is_rejection_text(trans.text):
                            self.tool_bridge.resolve_pending_approval(approved=False)
                            logger.info("hitl_approval_rejected_via_user_speech")

                elif event.event_type == LiveEventType.AUDIO_CHUNK:
                    chunk: LiveAudioChunk = event.payload
                    duration_sec = len(chunk.data) / (
                        chunk.sample_rate * 2
                    )  # 16-bit PCM = 2 bytes/sample
                    self.telemetry.log_audio_output(duration_sec)

                # Forward event to UI / consumer queue
                await self._outbound_events.put(event)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("error_in_voice_receive_loop", error=str(exc))
            self.telemetry.log_session_degraded(str(exc))

    async def send_user_speech(self, pcm_bytes: bytes, end_of_turn: bool = False) -> None:
        """Stream client microphone audio chunk to the voice model."""
        duration_sec = len(pcm_bytes) / (16000 * 2)  # 16kHz 16-bit mono PCM input
        self.telemetry.log_audio_input(duration_sec)
        await self.adapter.send_audio(pcm_bytes, end_of_turn=end_of_turn)

    async def send_user_text(self, text: str) -> None:
        """Send conversational text input into the voice session."""
        self.current_user_intent = text
        if self._is_approval_text(text):
            resolved_auth = self.tool_bridge.resolve_pending_approval(approved=True)
            if resolved_auth:
                logger.info(
                    "hitl_approval_resolved_via_user_text",
                    proposal_id=str(resolved_auth.proposal_id),
                    tool_id=resolved_auth.tool_id,
                )
        elif self._is_rejection_text(text):
            self.tool_bridge.resolve_pending_approval(approved=False)
            logger.info("hitl_approval_rejected_via_user_text")

        self.telemetry.log_turn_started(is_user=True)
        await self.adapter.send_text(text, end_of_turn=True)

    @staticmethod
    def _is_approval_text(text: str) -> bool:
        """Detect explicit user intent to grant human-in-the-loop approval."""
        if not text:
            return False
        clean = text.strip().lower()
        if clean in (
            "/approve",
            "approve",
            "approved",
            "yes",
            "confirm",
            "proceed",
            "authorize",
            "authorized",
            "run it",
            "execute",
        ):
            return True
        import re

        tokens = set(re.findall(r"\b\w+\b", clean))
        approval_tokens = {"approved", "approve", "authorized", "authorize", "proceed", "confirm"}
        return bool(tokens & approval_tokens)

    @staticmethod
    def _is_rejection_text(text: str) -> bool:
        """Detect explicit user intent to reject human-in-the-loop approval."""
        if not text:
            return False
        clean = text.strip().lower()
        if clean in (
            "/reject",
            "/deny",
            "reject",
            "rejected",
            "deny",
            "denied",
            "no",
            "disapprove",
        ):
            return True
        import re

        tokens = set(re.findall(r"\b\w+\b", clean))
        rejection_tokens = {"reject", "rejected", "deny", "denied", "disapprove"}
        return bool(tokens & rejection_tokens)

    async def interrupt(self) -> None:
        """Trigger immediate barge-in / speech cutoff."""
        self.telemetry.log_interrupt()
        await self.adapter.interrupt()

    async def _on_background_task_event(self, event: VoiceTaskEvent) -> None:
        """Handle background task milestone events and narrate truthful progress."""
        self.telemetry.log_task_progress(event.task_id, event.message)

        # Anti-spam throttling check
        now = datetime.now(UTC)
        elapsed = (now - self.last_proactive_broadcast).total_seconds()
        if elapsed < self.proactive_throttle_seconds and not event.event_type.name.endswith(
            ("COMPLETED", "FAILED", "CANCELLED")
        ):
            return

        # Do not interleave client text while a tool call is awaiting its tool response
        if self._active_tool_call:
            return

        self.last_proactive_broadcast = now

        # Send milestone notification to model for natural spoken synthesis
        notification = f"[System Progress Update for task '{event.title}']: {event.message}"
        try:
            await self.adapter.send_text(notification, end_of_turn=True)
        except Exception as e:
            logger.warning("failed_to_send_task_notification_to_voice", error=str(e))

    def get_event_stream(self) -> AsyncIterator[LiveEvent]:
        """Async generator yielding live output events (audio chunks, text, transcriptions)."""

        async def stream_generator() -> AsyncIterator[LiveEvent]:
            while self._is_running or not self._outbound_events.empty():
                try:
                    event = await asyncio.wait_for(self._outbound_events.get(), timeout=0.1)
                    yield event
                except TimeoutError:
                    continue

        return stream_generator()

    async def stop(self) -> None:
        """Gracefully terminate the voice agent and close session."""
        self._is_running = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        await self.session_manager.close_session()
        self.task_manager.unsubscribe(self._on_background_task_event)
        logger.info("live_voice_agent_stopped", session_id=self.session_id)
