"""Command Handlers for the JARVIS Unified CLI.

Implements CLI commands for:
- status: Diagnostics and system health overview
- run: Policy-gated task submission and execution
- gateway: WebSocket daemon startup
- cron: Heartbeat and periodic job execution
- acp: External coding agent session management
- serve: Unified server daemon (Gateway + Heartbeat + Control Plane)
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from rich import box
from rich.console import Console
from rich.panel import Panel

from jarvis.acp import (
    AcpSessionSpec,
    JarvisAgentBroker,
)
from jarvis.cognition.gemini_live import GeminiLiveBridge, LiveSessionState
from jarvis.config import settings
from jarvis.contracts.task import TaskState, TaskType
from jarvis.core.app_control_engine import app_control_engine
from jarvis.core.control_plane import ControlPlane, control_plane
from jarvis.cron import HeartbeatMonitor, heartbeat_monitor
from jarvis.execution.browser.host import browser_node
from jarvis.execution.windows.host import windows_node
from jarvis.execution.windows.system import get_system_info
from jarvis.gateway.server import GatewayServer
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger, set_console_logging
from jarvis.voice import (
    AudioInputGate,
    MicrophoneCapture,
    PcmStreamPlayer,
    VoiceState,
    VoiceStateMachine,
    get_default_input_device,
    voice_synthesizer,
)

console = Console(force_terminal=True, highlight=False)


def get_user_callsign() -> str:
    """Dynamically retrieve the configured user callsign."""
    return getattr(settings, "USER_CALLSIGN", "Sir")


def check_for_callsign_update(user_text: str) -> Optional[str]:
    """Detect if the user requested a callsign change dynamically."""
    lower = user_text.lower().strip()
    prefixes = ["address me as ", "call me as ", "call me "]
    for p in prefixes:
        if p in lower:
            idx = lower.find(p) + len(p)
            new_callsign = user_text[idx:].strip().strip(".!?,")
            if new_callsign:
                return new_callsign.capitalize()
    return None


def ensure_nodes_registered() -> None:
    """Ensure host and browser execution nodes are registered with the action broker."""
    windows_node.register_capabilities()
    browser_node.register_capabilities()


def handle_status(database: Optional[DatabaseEngine] = None) -> Dict[str, Any]:
    """Gather and report comprehensive JARVIS system diagnostics."""
    target_db = database or db

    # 1. System telemetry
    try:
        sys_info = get_system_info()
    except Exception as err:
        sys_info = {"error": str(err)}

    # 2. Database statistics
    db_stats: Dict[str, Any] = {}
    try:
        with target_db.transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM sessions;")
            db_stats["sessions_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM tasks;")
            db_stats["tasks_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM memory_records;")
            db_stats["memories_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM scheduled_jobs;")
            db_stats["jobs_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM artifacts;")
            db_stats["artifacts_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM acp_sessions;")
            db_stats["acp_sessions_count"] = cursor.fetchone()[0]
    except Exception as db_err:
        db_stats["error"] = str(db_err)

    status_report = {
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "database_path": str(target_db.db_path),
        "workspace_dir": str(settings.WORKSPACE_DIR),
        "database_stats": db_stats,
        "system_info": sys_info,
        "status": "OPERATIONAL",
    }
    return status_report


async def handle_run(
    intent: str,
    task_type: TaskType = TaskType.WINDOWS_CONTROL,
    session_id: Optional[str] = None,
    cp: Optional[ControlPlane] = None,
) -> Dict[str, Any]:
    """Submit a task to the Control Plane and execute until completion."""
    ensure_nodes_registered()
    plane = cp or control_plane
    logger.info(f"Submitting intent via CLI: '{intent}'")

    sess_id = session_id or "SESS-CLI"
    final_task = await plane.submit_intent(raw_intent=intent, session_id=sess_id)

    outcome = {
        "task_id": final_task.task_id,
        "state": final_task.state.value,
        "result_summary": final_task.result_summary,
        "verification_passed": final_task.verification_passed,
        "error_message": final_task.error_message,
    }
    return outcome


async def handle_gateway(
    host: Optional[str] = None,
    port: Optional[int] = None,
    server_instance: Optional[GatewayServer] = None,
) -> None:
    """Run the persistent WebSocket Gateway daemon."""
    server = server_instance or GatewayServer(host=host, port=port)
    await server.start()
    logger.info("Gateway daemon started. Press Ctrl+C to terminate.")
    try:
        while True:
            await asyncio.sleep(1.0)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Gateway shutdown signal received.")
    finally:
        await server.stop()


async def handle_cron(
    once: bool = True,
    interval_seconds: float = 60.0,
    monitor: Optional[HeartbeatMonitor] = None,
) -> List[Dict[str, Any]]:
    """Execute heartbeat and scheduled tasks."""
    mon = monitor or heartbeat_monitor
    results: List[Dict[str, Any]] = []

    if once:
        report = await mon.pulse()
        results.append(report.model_dump(mode="json"))
        return results

    logger.info(f"Starting continuous Heartbeat monitor (interval={interval_seconds}s)...")
    try:
        while True:
            report = await mon.pulse()
            results.append(report.model_dump(mode="json"))
            await asyncio.sleep(interval_seconds)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Heartbeat monitor stopped.")
    return results


async def handle_acp(
    subcommand: str,
    repo_path: Optional[str] = None,
    model: str = "GPT-OSS 120B",
    instruction: Optional[str] = None,
    broker_instance: Optional[JarvisAgentBroker] = None,
) -> Dict[str, Any]:
    """Manage ACP external coding agent sessions."""
    broker = broker_instance or JarvisAgentBroker()

    if subcommand == "spawn":
        if not repo_path:
            raise ValueError("--repo is required to spawn an ACP session.")
        spec = AcpSessionSpec(
            repo_path=Path(repo_path),
            model=model,
        )
        created = broker.create_session(spec)
        return {
            "session_id": created.session_id,
            "status": "created",
            "repo_path": str(created.repo_path),
            "model": created.model,
        }

    elif subcommand == "run":
        if not repo_path or not instruction:
            raise ValueError("Both --repo and --instruction are required for ACP run.")
        spec = AcpSessionSpec(
            repo_path=Path(repo_path),
            model=model,
        )
        created = broker.create_session(spec)
        res = await broker.execute_turn(created.session_id, instruction=instruction)
        return {
            "session_id": created.session_id,
            "status": res.status,
            "summary": res.summary,
            "changed_files": res.changed_files,
            "tests_passed": res.tests_passed,
        }

    else:
        raise ValueError(f"Unknown ACP subcommand '{subcommand}' (supported: spawn, run)")


async def handle_serve(
    host: Optional[str] = None,
    port: Optional[int] = None,
    heartbeat_interval: float = 60.0,
) -> None:
    """Run the unified system server daemon: Gateway + Heartbeat Monitor."""
    ensure_nodes_registered()
    server = GatewayServer(host=host, port=port)
    await server.start()
    logger.info("JARVIS Unified Server started (Gateway + Heartbeat). Press Ctrl+C to terminate.")

    async def heartbeat_loop() -> None:
        while True:
            try:
                await heartbeat_monitor.pulse()
            except Exception as err:
                logger.error(f"Heartbeat pulse error: {err}")
            await asyncio.sleep(heartbeat_interval)

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        while True:
            await asyncio.sleep(1.0)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutdown signal received. Terminating unified server...")
    finally:
        heartbeat_task.cancel()
        await server.stop()
        logger.info("JARVIS Unified Server stopped cleanly.")


async def handle_chat(
    session_id: Optional[str] = None,
    live_mode: bool = False,
    message: Optional[str] = None,
    with_daemon: bool = False,
    cp: Optional[ControlPlane] = None,
) -> Optional[Dict[str, Any]]:
    """Conduct an interactive or single-turn real-time dialogue session with JARVIS."""
    ensure_nodes_registered()

    daemon_tasks: List[asyncio.Task[Any]] = []
    gateway_server: Optional[GatewayServer] = None

    if with_daemon:
        try:
            gateway_server = GatewayServer()
            await gateway_server.start()
            logger.info(
                f"Gateway daemon active in background (ws://{gateway_server.host}:{gateway_server.port})."
            )
        except Exception as gw_err:
            logger.warning(f"Notice: Background gateway daemon initialization warning: {gw_err}")

        async def heartbeat_loop() -> None:
            while True:
                try:
                    await heartbeat_monitor.pulse()
                except Exception as err:
                    logger.debug(f"Heartbeat pulse error: {err}")
                await asyncio.sleep(60.0)

        daemon_tasks.append(asyncio.create_task(heartbeat_loop()))

    try:
        if live_mode:
            if not settings.GEMINI_API_KEY:
                print("Error: GEMINI_API_KEY is not configured in environment.")
                return {"error": "Missing GEMINI_API_KEY"}

            set_console_logging(False)
            bridge = GeminiLiveBridge(session_id=session_id)
            voice_state_machine = VoiceStateMachine(initial_state=VoiceState.IDLE)
            input_gate = AudioInputGate(state_machine=voice_state_machine)
            mic = MicrophoneCapture(
                samplerate=settings.AUDIO_INPUT_SAMPLE_RATE,
                channels=settings.AUDIO_INPUT_CHANNELS,
                chunk_ms=settings.AUDIO_INPUT_CHUNK_MS,
                device_index=settings.AUDIO_INPUT_DEVICE_INDEX,
                speech_threshold=settings.AUDIO_VAD_ENERGY_THRESHOLD,
            )
            pcm_player = PcmStreamPlayer(
                samplerate=settings.AUDIO_OUTPUT_SAMPLE_RATE,
                on_playback_state_change=lambda is_playing: mic.set_duplex_suppression(
                    is_playing and settings.AUDIO_DUPLEX_SUPPRESSION
                ),
            )
            audio_chunk_count = 0
            turn_text_chunks: List[str] = []
            turn_finished = asyncio.Event()

            async def on_audio(chunk: bytes) -> None:
                nonlocal audio_chunk_count
                audio_chunk_count += 1
                if voice_state_machine.state != VoiceState.SPEAKING:
                    pcm_player.start_stream()
                    voice_state_machine.transition(VoiceState.SPEAKING, reason="Model audio stream")
                pcm_player.play_chunk(chunk)

            async def on_tool_call(name: str, args: Dict[str, Any]) -> None:
                if name == "web_search":
                    query = args.get("query", "")
                    console.print(f"\n[bold cyan]>> [Web Search: '{query}']...[/bold cyan]")
                elif name == "shell_execute":
                    cmd = args.get("command", "")
                    console.print(f"\n[bold cyan]>> [Running Shell Command: {cmd}][/bold cyan]")
                elif name == "delegate_task":
                    target = args.get("target_model", "Specialist Model")
                    task = args.get("task", "")
                    console.print(
                        f"\n[bold cyan]>> [Delegating Task to {target}: '{task}']...[/bold cyan]"
                    )
                elif name == "browser_navigate":
                    url = args.get("url", "")
                    console.print(f"\n[bold cyan]>> [Browser Navigating to: {url}][/bold cyan]")
                elif name == "browser_type":
                    sel = args.get("selector", "")
                    text = args.get("text", "")
                    enter = args.get("press_enter", False)
                    console.print(
                        f"\n[bold cyan]>> [Browser Typing: '{text}' into '{sel}' (press_enter={enter})][/bold cyan]"
                    )
                elif name == "browser_click":
                    sel = args.get("selector", "")
                    console.print(f"\n[bold cyan]>> [Browser Clicking: '{sel}'][/bold cyan]")
                elif name == "browser_snapshot":
                    console.print(
                        "\n[bold cyan]>> [Browser Snapshot: Inspecting interactive DOM elements...][/bold cyan]"
                    )
                elif name == "browser_scroll":
                    direction = args.get("direction", "down")
                    amount = args.get("amount", 300)
                    console.print(
                        f"\n[bold cyan]>> [Browser Scrolling: {direction} by {amount}px][/bold cyan]"
                    )
                elif name == "app_launch":
                    target = args.get("target", "")
                    arguments = args.get("arguments", [])
                    arg_str = f" with args {arguments}" if arguments else ""
                    console.print(
                        f"\n[bold cyan]>> [Launching Application: {target}{arg_str}][/bold cyan]"
                    )
                elif name == "app_focus":
                    target = args.get("target", "")
                    console.print(f"\n[bold cyan]>> [Focusing Window: {target}][/bold cyan]")
                elif name == "app_close":
                    target = args.get("target", "")
                    console.print(f"\n[bold cyan]>> [Closing Application: {target}][/bold cyan]")
                elif name == "ui_inspect":
                    target = args.get("window_target", "foreground window")
                    console.print(
                        f"\n[bold cyan]>> [UI Inspect: Traversing controls in '{target}'...][/bold cyan]"
                    )
                elif name == "ui_interact":
                    target = args.get("window_target", "")
                    query = args.get("element_query", "")
                    action = args.get("action", "click")
                    console.print(
                        f"\n[bold cyan]>> [UI Interact: {action} on '{query}' in '{target}'][/bold cyan]"
                    )
                elif name == "computer_action":
                    action = args.get("action", "")
                    coords = args.get("coordinate", "")
                    text = args.get("text", "")
                    keys = args.get("keys", "")
                    details = f"action={action}"
                    if coords:
                        details += f", coords={coords}"
                    if text:
                        details += f", text='{text}'"
                    if keys:
                        details += f", keys='{keys}'"
                    console.print(f"\n[bold cyan]>> [Computer Action: {details}][/bold cyan]")
                elif name == "system_info":
                    console.print(
                        "\n[bold cyan]>> [Checking system hardware and OS status...][/bold cyan]"
                    )
                elif name == "system_screenshot":
                    console.print("\n[bold cyan]>> [Capturing desktop screenshot...][/bold cyan]")
                elif name == "system_volume_set":
                    level = args.get("level", "")
                    console.print(f"\n[bold cyan]>> [Setting volume to {level}%][/bold cyan]")
                elif name == "system_volume_get":
                    console.print("\n[bold cyan]>> [Checking master volume level...][/bold cyan]")
                else:
                    args_summary = ", ".join(f"{k}={v!r}" for k, v in args.items())
                    console.print(f"\n[bold cyan]>> [Executing {name}({args_summary})][/bold cyan]")

            async def on_text(chunk: str) -> None:
                turn_text_chunks.append(chunk)

            async def on_turn_complete() -> None:
                nonlocal audio_chunk_count
                pcm_player.mark_generation_finished()
                await pcm_player.wait_until_drained(timeout=settings.DEFAULT_TIMEOUT_SECONDS)
                pcm_player.mark_idle()
                voice_state_machine.transition(VoiceState.LISTENING, reason="Playback drained")

                full_text = "".join(turn_text_chunks).strip()
                if full_text:
                    console.print(
                        Panel(
                            full_text,
                            title="[bold cyan]JARVIS[/bold cyan]",
                            border_style="bright_blue",
                            box=box.ROUNDED,
                            padding=(0, 2),
                        )
                    )
                    console.print("")
                elif audio_chunk_count > 0:
                    console.print(
                        Panel(
                            f"[italic cyan]Spoken voice response completed ({audio_chunk_count} audio chunks)[/italic cyan]",
                            title="[bold cyan]JARVIS[/bold cyan]",
                            border_style="bright_blue",
                            box=box.ROUNDED,
                            padding=(0, 2),
                        )
                    )
                    console.print("")
                turn_text_chunks.clear()
                audio_chunk_count = 0
                turn_finished.set()
                console.print("[dim cyan]>> [JARVIS is listening... speak or type][/dim cyan]\n")

            async def on_interrupted() -> None:
                callsign = get_user_callsign()
                voice_state_machine.transition(
                    VoiceState.INTERRUPTED, reason=f"{callsign} barge-in"
                )
                pcm_player.interrupt()
                voice_state_machine.transition(VoiceState.LISTENING, reason="Interruption reset")
                console.print(
                    f"\n[bold yellow]>> [{callsign} Interrupted - Listening...][/bold yellow]\n"
                )

            async def on_input_transcription(text: str, finished: bool) -> None:
                if text:
                    callsign = get_user_callsign()
                    console.print(
                        f"\r[bold green]{callsign} (Voice) > [/bold green][green]{text}[/green]"
                    )
                if finished and text:
                    new_callsign = check_for_callsign_update(text)
                    if new_callsign:
                        settings.USER_CALLSIGN = new_callsign
                        logger.info(f"User callsign updated dynamically to: {new_callsign}")

            bridge.audio_chunk_handler = on_audio
            bridge.text_chunk_handler = on_text
            bridge.turn_complete_handler = on_turn_complete
            bridge.tool_call_handler = on_tool_call
            bridge.interrupted_handler = on_interrupted
            bridge.input_transcription_handler = on_input_transcription

            # Microphone capture streaming task
            mic_task: Optional[asyncio.Task[None]] = None
            stop_mic_event = asyncio.Event()

            async def mic_streaming_loop() -> None:
                while not stop_mic_event.is_set():
                    try:
                        frame = await mic.read_frame()
                        if input_gate.should_transmit(frame):
                            if bridge.state == LiveSessionState.ACTIVE:
                                await bridge.send_audio_chunk(frame.pcm_bytes)
                    except asyncio.CancelledError:
                        break
                    except Exception as err:
                        logger.debug(f"Microphone streaming loop error: {err}")
                        await asyncio.sleep(0.01)

            default_mic = get_default_input_device()
            mic_label = (
                f"[green]{default_mic.get('name', 'Default Microphone')}[/green] [dim]({settings.AUDIO_INPUT_SAMPLE_RATE}Hz mono)[/dim]"
                if default_mic
                else "[yellow]No microphone detected[/yellow]"
            )

            live_header = (
                f"[bold white]Session:[/bold white] [cyan]{bridge.session_id}[/cyan] | "
                f"[bold white]Voice:[/bold white] [cyan]{bridge.voice_name}[/cyan] | "
                f"[bold white]Voice Mode:[/bold white] [green]HALF-DUPLEX / ECHO-SAFE[/green]\n"
                f"[bold white]Approvals:[/bold white] [green]BYPASSED (LIVE)[/green] | "
                f"[bold white]Core Model:[/bold white] [cyan]Gemini 3.8 Live Multimodal (Audio/Text/Vision)[/cyan]\n"
                f"[bold white]Microphone:[/bold white] {mic_label}\n"
                f"[bold white]Capabilities:[/bold white] [cyan]Live Web Search + Windows Native + Browser Automation[/cyan]\n"
                f"[bold white]Delegation:[/bold white] [cyan]GPT-OSS 120B & Qwen 3.8 27B Specialist Models[/cyan]\n"
                f"[bold white]Input Modes:[/bold white] [cyan]Speak into Mic (Realtime VAD) OR Type in Console[/cyan]\n"
                f"[bold white]Commands:[/bold white] [dim]Type 'exit' to quit | /image <path> for vision inputs[/dim]"
            )
            console.print(
                Panel(
                    live_header,
                    title="[bold cyan]JARVIS OPERATING SYSTEM (GEMINI LIVE)[/bold cyan]",
                    border_style="bright_blue",
                    box=box.ROUNDED,
                    padding=(1, 2),
                )
            )
            console.print("")

            await bridge.connect()
            voice_state_machine.transition(VoiceState.LISTENING, reason="Session connected")

            mic_active = False
            try:
                mic.start()
                mic_task = asyncio.create_task(mic_streaming_loop())
                mic_active = True
            except Exception as mic_err:
                console.print(
                    f"[bold yellow]Notice: Microphone unavailable: {mic_err}[/bold yellow]\n"
                )

            try:
                callsign = get_user_callsign()
                if message:
                    console.print(
                        f"[bold green]{callsign} > [/bold green][green]{message}[/green]\n"
                    )
                    turn_finished.clear()
                    audio_chunk_count = 0
                    turn_text_chunks.clear()
                    voice_state_machine.transition(
                        VoiceState.THINKING, reason=f"{callsign} direct message"
                    )
                    await bridge.send_text(message)
                    try:
                        await asyncio.wait_for(
                            turn_finished.wait(), timeout=settings.DEFAULT_TIMEOUT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        pass
                    return {"session_id": bridge.session_id, "audio_chunks": audio_chunk_count}

                while True:
                    callsign = get_user_callsign()
                    try:
                        sys.stdout.write(f"\033[1;32m{callsign} > \033[32m")
                        sys.stdout.flush()
                        user_input = await asyncio.to_thread(input)
                    except (EOFError, KeyboardInterrupt):
                        console.print(
                            Panel(
                                f"Concluding live streaming session. Standing by, {callsign}.",
                                title="[bold cyan]JARVIS[/bold cyan]",
                                border_style="bright_blue",
                                box=box.ROUNDED,
                                padding=(0, 2),
                            )
                        )
                        break
                    finally:
                        sys.stdout.write("\033[0m")
                        sys.stdout.flush()

                    user_input = user_input.strip()
                    if not user_input:
                        continue

                    # Dynamic honorific/callsign update check
                    new_callsign = check_for_callsign_update(user_input)
                    if new_callsign:
                        settings.USER_CALLSIGN = new_callsign
                        callsign = new_callsign
                        logger.info(f"User callsign updated dynamically to: {new_callsign}")

                    if user_input.lower() in ("exit", "quit", "q"):
                        console.print(
                            Panel(
                                f"Terminating live stream. Standing by, {callsign}.",
                                title="[bold cyan]JARVIS[/bold cyan]",
                                border_style="bright_blue",
                                box=box.ROUNDED,
                                padding=(0, 2),
                            )
                        )
                        break

                    # Multimodal Image Input support: /image <path> [prompt]
                    if user_input.startswith("/image ") or user_input.startswith("/img "):
                        parts = user_input.split(maxsplit=2)
                        if len(parts) < 2:
                            console.print(
                                "[yellow]Usage: /image <file_path> [optional prompt][/yellow]\n"
                            )
                            continue
                        img_path_str = parts[1]
                        prompt_str = (
                            parts[2]
                            if len(parts) > 2
                            else "Analyze this image and describe what you see."
                        )
                        img_path = Path(img_path_str)
                        if not img_path.exists():
                            console.print(
                                f"[bold red]Error: Image file '{img_path_str}' not found.[/bold red]\n"
                            )
                            continue
                        mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
                        img_bytes = img_path.read_bytes()
                        console.print(
                            f"\n[bold cyan][Uploading {img_path.name} ({len(img_bytes)} bytes)...][/bold cyan]\n"
                        )
                        turn_finished.clear()
                        audio_chunk_count = 0
                        turn_text_chunks.clear()
                        voice_state_machine.transition(
                            VoiceState.THINKING, reason=f"{callsign} image input"
                        )
                        await bridge.send_image(
                            image_bytes=img_bytes, mime_type=mime, prompt=prompt_str
                        )
                        try:
                            await asyncio.wait_for(
                                turn_finished.wait(), timeout=settings.DEFAULT_TIMEOUT_SECONDS
                            )
                        except asyncio.TimeoutError:
                            pass
                        continue

                    turn_finished.clear()
                    audio_chunk_count = 0
                    turn_text_chunks.clear()
                    voice_state_machine.transition(
                        VoiceState.THINKING, reason=f"{callsign} text input"
                    )
                    await bridge.send_text(user_input)
                    try:
                        await asyncio.wait_for(
                            turn_finished.wait(), timeout=settings.DEFAULT_TIMEOUT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        pass

            finally:
                stop_mic_event.set()
                if mic_task and not mic_task.done():
                    mic_task.cancel()
                    try:
                        await mic_task
                    except asyncio.CancelledError:
                        pass
                if mic_active:
                    mic.stop()
                pcm_player.stop()
                voice_state_machine.transition(VoiceState.STOPPING, reason="Live session shutdown")
                voice_state_machine.transition(VoiceState.IDLE, reason="Live session stopped")
                await bridge.disconnect()
                set_console_logging(True)
                console.print("[dim]JARVIS Live session closed cleanly.[/dim]\n")
            return None

        # Standard Interactive Dialogue via Control Plane
        plane = cp or control_plane
        sess_id = session_id or f"SESS-CHAT-{uuid4().hex[:8].upper()}"

        callsign = get_user_callsign()
        if message:
            logger.info(f"Submitting chat message: '{message}'")
            console.print(f"[bold green]{callsign} > [/bold green][green]{message}[/green]\n")
            final_task = await plane.submit_intent(raw_intent=message, session_id=sess_id)
            response_text = (
                final_task.result_summary or final_task.error_message or "Task processed."
            )
            console.print(
                Panel(
                    response_text,
                    title="[bold cyan]JARVIS[/bold cyan]",
                    border_style="bright_blue",
                    box=box.ROUNDED,
                    padding=(0, 2),
                )
            )
            console.print("")
            voice_synthesizer.speak(response_text)
            return {
                "session_id": sess_id,
                "task_id": final_task.task_id,
                "state": final_task.state.value,
                "response": response_text,
            }

        # Boot greeting
        greeting = f"JARVIS operational. Standing by for your command, {callsign}."
        voice_synthesizer.speak(greeting)

        chat_banner = (
            f"[bold white]Session ID:[/bold white] [cyan]{sess_id}[/cyan]\n"
            f"[bold white]Voice Synthesis:[/bold white] [green]ACTIVE[/green] | "
            f"[bold white]Native Nodes:[/bold white] [cyan]WINDOWS + BROWSER[/cyan]\n"
            f"[bold white]Control Plane:[/bold white] [cyan]Autonomous Intent Formulation & Action Broker[/cyan]\n"
            f"[bold white]Commands:[/bold white] [dim]Type any instruction, query, or 'exit' to quit[/dim]"
        )
        console.print(
            Panel(
                chat_banner,
                title="[bold cyan]JARVIS OPERATING SYSTEM ONLINE[/bold cyan]",
                border_style="bright_blue",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
        console.print("")

        while True:
            callsign = get_user_callsign()
            try:
                sys.stdout.write(f"\033[1;32m{callsign} > \033[32m")
                sys.stdout.flush()
                user_input = await asyncio.to_thread(input)
            except (EOFError, KeyboardInterrupt):
                console.print(
                    Panel(
                        f"Concluding interactive session. Standing by, {callsign}.",
                        title="[bold cyan]JARVIS[/bold cyan]",
                        border_style="bright_blue",
                        box=box.ROUNDED,
                        padding=(0, 2),
                    )
                )
                break
            finally:
                sys.stdout.write("\033[0m")
                sys.stdout.flush()

            user_input = user_input.strip()
            if not user_input:
                continue

            new_callsign = check_for_callsign_update(user_input)
            if new_callsign:
                settings.USER_CALLSIGN = new_callsign
                callsign = new_callsign
                logger.info(f"User callsign updated dynamically to: {new_callsign}")

            if user_input.lower() in ("exit", "quit", "q"):
                farewell = f"Interactive session concluded. Standing by, {callsign}."
                console.print(
                    Panel(
                        farewell,
                        title="[bold cyan]JARVIS[/bold cyan]",
                        border_style="bright_blue",
                        box=box.ROUNDED,
                        padding=(0, 2),
                    )
                )
                console.print("")
                voice_synthesizer.speak(farewell)
                break

            console.print("[dim cyan]JARVIS is processing...[/dim cyan]")
            final_task = await plane.submit_intent(raw_intent=user_input, session_id=sess_id)
            if final_task.state == TaskState.COMPLETED:
                answer = final_task.result_summary or "Task completed successfully."
                console.print(
                    Panel(
                        answer,
                        title="[bold cyan]JARVIS[/bold cyan]",
                        border_style="bright_blue",
                        box=box.ROUNDED,
                        padding=(0, 2),
                    )
                )
                console.print("")
                voice_synthesizer.speak(answer)
            else:
                err = final_task.error_message or "Action could not be completed."
                console.print(
                    Panel(
                        f"[bold red]Error:[/bold red] {err}",
                        title="[bold red]JARVIS Notice[/bold red]",
                        border_style="red",
                        box=box.ROUNDED,
                        padding=(0, 2),
                    )
                )
                console.print("")
                voice_synthesizer.speak(f"Notice: {err}")

        return None

    finally:
        for t in daemon_tasks:
            t.cancel()
        if gateway_server:
            await gateway_server.stop()


def handle_app_launch(target: str, arguments: Optional[List[str]] = None) -> Dict[str, Any]:
    """Launch an application and wait for window readiness."""
    ensure_nodes_registered()
    res = app_control_engine.launch_application(target=target, arguments=arguments)
    return res.to_dict()


def handle_app_focus(target: str) -> Dict[str, Any]:
    """Focus an open application window."""
    ensure_nodes_registered()
    res = app_control_engine.focus_window(target=target)
    return res.to_dict()


def handle_app_close(target: str) -> Dict[str, Any]:
    """Close an application window."""
    ensure_nodes_registered()
    res = app_control_engine.close_window(target=target)
    return res.to_dict()


def handle_app_windows() -> List[Dict[str, Any]]:
    """List open desktop windows."""
    ensure_nodes_registered()
    return app_control_engine.list_windows()


def handle_app_inspect(window_target: Optional[str] = None, max_depth: int = 5) -> Dict[str, Any]:
    """Inspect the UI tree of a target window."""
    ensure_nodes_registered()
    return app_control_engine.inspect_ui(window_target=window_target, max_depth=max_depth)


def handle_app_interact(
    window_target: str,
    element_query: str,
    action: str = "click",
    value: Optional[str] = None,
) -> Dict[str, Any]:
    """Interact with an element inside a window."""
    ensure_nodes_registered()
    res = app_control_engine.interact(
        window_target=window_target,
        element_query=element_query,
        action=action,
        value=value,
    )
    return res.to_dict()
