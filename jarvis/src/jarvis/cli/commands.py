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
from jarvis.core.control_plane import ControlPlane, control_plane
from jarvis.cron import HeartbeatMonitor, heartbeat_monitor
from jarvis.execution.browser.host import browser_node
from jarvis.execution.windows.host import windows_node
from jarvis.execution.windows.system import get_system_info
from jarvis.gateway.server import GatewayServer
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger, set_console_logging
from jarvis.voice import (
    MicrophoneCapture,
    PcmStreamPlayer,
    get_default_input_device,
    voice_synthesizer,
)

console = Console(force_terminal=True, highlight=False)


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
        gateway_server = GatewayServer()
        await gateway_server.start()
        logger.info("Gateway daemon started in background (ws://127.0.0.1:18789).")

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
                pcm_player.play_chunk(chunk)

            async def on_tool_call(name: str, args: Dict[str, Any]) -> None:
                if name == "web_search":
                    query = args.get("query", "")
                    console.print(f"\n[bold cyan]>> [Searching web: '{query}']...[/bold cyan]")
                elif name == "shell_execute":
                    cmd = args.get("command", "")
                    console.print(f"\n[bold cyan]>> [Running: {cmd}][/bold cyan]")
                elif name == "delegate_task":
                    target = args.get("target_model", "Specialist Model")
                    console.print(f"\n[bold cyan]>> [Delegating to {target}...][/bold cyan]")
                elif name == "browser_navigate":
                    url = args.get("url", "")
                    console.print(f"\n[bold cyan]>> [Navigating Browser: {url}][/bold cyan]")
                elif name == "system_info":
                    console.print(
                        "\n[bold cyan]>> [Checking system hardware and OS status...][/bold cyan]"
                    )
                elif name == "system_screenshot":
                    console.print(
                        "\n[bold cyan]>> [Capturing primary desktop screenshot...][/bold cyan]"
                    )
                elif name == "system_volume_set":
                    level = args.get("level", "")
                    console.print(f"\n[bold cyan]>> [Setting volume to {level}%][/bold cyan]")
                else:
                    console.print(f"\n[bold cyan]>> [Executing: {name}][/bold cyan]")

            async def on_text(chunk: str) -> None:
                turn_text_chunks.append(chunk)

            async def on_turn_complete() -> None:
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
                turn_finished.set()

            async def on_interrupted() -> None:
                pcm_player.interrupt()
                console.print(
                    "\n[bold yellow]>> [Operator Interrupted - Listening...][/bold yellow]\n"
                )

            async def on_input_transcription(text: str, finished: bool) -> None:
                if text:
                    console.print(
                        f"\r[bold green]Operator (Voice) > [/bold green][green]{text}[/green]"
                    )

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
                        chunk = await mic.read_chunk()
                        if chunk and bridge.state == LiveSessionState.ACTIVE:
                            await bridge.send_audio_chunk(chunk)
                    except asyncio.CancelledError:
                        break
                    except Exception as err:
                        logger.debug(f"Microphone streaming loop error: {err}")
                        await asyncio.sleep(0.05)

            default_mic = get_default_input_device()
            mic_label = (
                f"[green]{default_mic.get('name', 'Default Microphone')}[/green] [dim]({settings.AUDIO_INPUT_SAMPLE_RATE}Hz mono)[/dim]"
                if default_mic
                else "[yellow]No microphone detected[/yellow]"
            )

            live_header = (
                f"[bold white]Session:[/bold white] [cyan]{bridge.session_id}[/cyan] | "
                f"[bold white]Voice:[/bold white] [cyan]{bridge.voice_name}[/cyan] | "
                f"[bold white]Approvals:[/bold white] [green]BYPASSED (LIVE)[/green]\n"
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
                if message:
                    console.print(f"[bold green]Operator > [/bold green][green]{message}[/green]\n")
                    turn_finished.clear()
                    audio_chunk_count = 0
                    turn_text_chunks.clear()
                    await bridge.send_text(message)
                    try:
                        await asyncio.wait_for(
                            turn_finished.wait(), timeout=settings.DEFAULT_TIMEOUT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        pass
                    return {"session_id": bridge.session_id, "audio_chunks": audio_chunk_count}

                while True:
                    try:
                        sys.stdout.write("\033[1;32mOperator > \033[32m")
                        sys.stdout.flush()
                        user_input = await asyncio.to_thread(input)
                    except (EOFError, KeyboardInterrupt):
                        console.print(
                            Panel(
                                "Concluding live streaming session. Standing by, Operator.",
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
                    if user_input.lower() in ("exit", "quit", "q"):
                        console.print(
                            Panel(
                                "Terminating live stream. Standing by, Operator.",
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
                await bridge.disconnect()
                set_console_logging(True)
                console.print("[dim]JARVIS Live session closed cleanly.[/dim]\n")
            return None

        # Standard Interactive Dialogue via Control Plane
        plane = cp or control_plane
        sess_id = session_id or f"SESS-CHAT-{uuid4().hex[:8].upper()}"

        if message:
            logger.info(f"Submitting chat message: '{message}'")
            console.print(f"[bold green]Operator > [/bold green][green]{message}[/green]\n")
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
        greeting = "JARVIS operational. Standing by for your command, Operator."
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
            try:
                sys.stdout.write("\033[1;32mOperator > \033[32m")
                sys.stdout.flush()
                user_input = await asyncio.to_thread(input)
            except (EOFError, KeyboardInterrupt):
                console.print(
                    Panel(
                        "Concluding interactive session. Standing by, Operator.",
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
            if user_input.lower() in ("exit", "quit", "q"):
                farewell = "Interactive session concluded. Standing by, Operator."
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
