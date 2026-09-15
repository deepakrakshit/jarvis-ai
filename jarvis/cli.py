"""JARVIS Terminal User Interface and Interactive Runtime CLI.

Launches an interactive prompt session where users can talk to JARVIS,
assign real tasks, observe real-time execution steps, and review full session logs.
"""

import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from jarvis.core.policy.decision import AutonomyLevel
from jarvis.core.session.session_manager import SessionManager
from jarvis.orchestrator import JarvisOrchestrator

# Ensure UTF-8 output encoding across all terminals (especially Windows cmd/powershell)
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(force_terminal=True, highlight=False)


def print_banner() -> None:
    banner_text = (
        "[bold cyan]JARVIS v1.0.0[/bold cyan] - [italic white]Stateful Personal AI Operating System[/italic white]\n"
        "[dim]Zero-Trust Architecture | 5 Capability Specialists | Action Broker | Point-in-Time Reality[/dim]\n"
        "[dim]Type [bold yellow]exit[/bold yellow] or [bold yellow]quit[/bold yellow] to leave. Full logs saved to [bold green]sessions.json[/bold green] & [bold green]conversations.json[/bold green][/dim]"
    )
    console.print(Panel(banner_text, border_style="cyan", expand=False))


async def run_cli() -> None:
    """Main interactive terminal loop."""
    print_banner()

    session_mgr = SessionManager(
        sessions_path=Path("sessions.json"),
        conversations_path=Path("conversations.json"),
    )
    session = session_mgr.create_session(
        user_id="operator",
        metadata={"interface": "terminal_cli"},
    )
    console.print(f"[dim]Active Session ID:[/dim] [bold green]{session.session_id}[/bold green]\n")

    orchestrator = JarvisOrchestrator(
        session_manager=session_mgr,
        autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
    )

    def on_progress(component: str, message: str) -> None:
        colors = {
            "gateway": "dim white",
            "router": "bold blue",
            "specialist": "cyan",
            "firewall": "yellow",
            "policy_engine": "magenta",
            "action_broker": "green",
        }
        color = colors.get(component, "white")
        console.print(f"  [{color}]* [{component}][/{color}] [dim]{message}[/dim]")

    # Check for one-shot command line argument
    if len(sys.argv) > 1:
        one_shot_msg = " ".join(sys.argv[1:])
        console.print(f"[bold cyan]User>[/bold cyan] {one_shot_msg}")
        response = await orchestrator.interact(
            session_id=session.session_id,
            user_message=one_shot_msg,
            on_progress=on_progress,
        )
        console.print()
        console.print(
            Panel(Markdown(response), title="[bold cyan]JARVIS[/bold cyan]", border_style="blue")
        )
        session_mgr.close_session(session.session_id)
        return

    # Interactive prompt loop
    try:
        while True:
            try:
                user_msg = console.input("\n[bold cyan]User>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[yellow]Session interrupted by user.[/yellow]")
                break

            if not user_msg:
                continue

            if user_msg.lower() in ("exit", "quit", "q", "bye"):
                console.print("[dim]Closing session...[/dim]")
                break

            console.print()
            response = await orchestrator.interact(
                session_id=session.session_id,
                user_message=user_msg,
                on_progress=on_progress,
            )
            console.print()
            console.print(
                Panel(
                    Markdown(response), title="[bold cyan]JARVIS[/bold cyan]", border_style="blue"
                )
            )

    finally:
        session_mgr.close_session(session.session_id)
        latest_sess = session_mgr.get_session(session.session_id)
        turns = latest_sess.turns_count if latest_sess else 0
        tasks = latest_sess.tasks_executed if latest_sess else 0
        console.print(
            f"\n[bold green]Session {session.session_id} closed.[/bold green] "
            f"({turns} turns, {tasks} tasks audited and persisted to sessions.json & conversations.json)\n"
        )


def main() -> None:
    asyncio.run(run_cli())


if __name__ == "__main__":
    main()
