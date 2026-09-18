"""JARVIS Terminal User Interface and Interactive Runtime CLI.

Launches an interactive prompt session where users can talk to JARVIS,
assign real tasks, observe real-time execution steps, and review full session logs.
"""

import asyncio
import sys
import time
from pathlib import Path

try:
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]

try:
    import select
except ImportError:
    select = None  # type: ignore[assignment]

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


def read_user_input(console: Console, prompt: str = "\n[bold cyan]User>[/bold cyan] ") -> str:
    """Read user input supporting both single-line and multiline pasted text seamlessly.

    Features:
    1. Automatic paste detection: When multiple lines are pasted into the terminal,
       all buffered lines are automatically drained and combined into a single prompt.
    2. Explicit multiline mode: Entering '/paste' or '/multiline' allows entering or
       pasting multi-paragraph blocks ending on an empty line or '/end'.
    3. Block quotes: Starting with triple quotes ('\"\"\"' or "'''") collects lines until
       the matching closing quote.
    4. Line continuation: Ending a line with a backslash ('\\') continues on the next line.
    """
    first_line = console.input(prompt)
    stripped = first_line.strip()

    # 1. Explicit /paste or /multiline command
    if stripped.lower() in ("/paste", "/multiline"):
        console.print(
            "[dim cyan]Multiline mode active. Paste your text, then press Enter on an empty line (or type /end) to submit:[/dim cyan]"
        )
        paste_lines: list[str] = []
        while True:
            try:
                line = console.input("[dim]... [/dim]")
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip().lower() == "/end":
                break
            if not line.strip() and paste_lines:
                break
            paste_lines.append(line)
        return "\n".join(paste_lines).strip()

    # 2. Triple-quote block delimiter (""" or ''')
    if stripped.startswith(('"""', "'''")):
        quote = stripped[:3]
        if len(stripped) > 3 and stripped.endswith(quote):
            return stripped[3:-3].strip()
        lines = [first_line[3:]]
        while True:
            try:
                line = console.input("[dim]... [/dim]")
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip().endswith(quote):
                cleaned = line.strip()[:-3]
                if cleaned:
                    lines.append(cleaned)
                break
            lines.append(line)
        return "\n".join(lines).strip()

    # 3. Trailing backslash line continuation
    lines = [first_line]
    current = first_line
    while current.rstrip().endswith("\\"):
        lines[-1] = current.rstrip()[:-1].rstrip()
        try:
            current = console.input("[dim]... [/dim]")
            lines.append(current)
        except (EOFError, KeyboardInterrupt):
            break

    # 4. Automatic paste detection:
    # If the user pasted multiple lines, subsequent lines are already buffered in the
    # OS / console input stream. Drain all buffered lines immediately.
    if sys.stdin.isatty():
        time.sleep(0.02)
        pasted_count = 0
        while True:
            has_pending = False
            try:
                if msvcrt is not None:
                    has_pending = bool(msvcrt.kbhit())
                elif select is not None and hasattr(select, "select"):
                    r, _, _ = select.select([sys.stdin], [], [], 0.02)
                    has_pending = bool(r)
            except Exception:
                has_pending = False

            if not has_pending:
                break

            try:
                line = sys.stdin.readline()
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
                pasted_count += 1
            except Exception:
                break

        if pasted_count > 0:
            console.print(f"[dim]✓ Pasted {len(lines)} lines received as single prompt[/dim]")
    else:
        # Non-interactive / piped stream
        try:
            remaining = sys.stdin.read()
            if remaining:
                for rem_line in remaining.splitlines():
                    if rem_line.strip():
                        lines.append(rem_line)
        except Exception:
            pass

    return "\n".join(lines).strip()


def print_banner() -> None:
    banner_text = (
        "[bold cyan]JARVIS v1.0.0[/bold cyan] - [italic white]Stateful Personal AI Operating System[/italic white]\n"
        "[dim]Zero-Trust Architecture | 5 Capability Specialists | Action Broker | Point-in-Time Reality[/dim]\n"
        '[dim]Multiline: Direct paste supported, or use [bold yellow]/paste[/bold yellow] (or [bold yellow]"""[/bold yellow]) | [bold yellow]exit[/bold yellow] to leave[/dim]'
    )
    console.print(Panel(banner_text, border_style="cyan", expand=False))


async def run_cli() -> None:
    """Main interactive terminal loop."""
    if "--voice" in sys.argv or "-v" in sys.argv:
        from jarvis.voice_cli import run_voice_plane

        await run_voice_plane()
        return

    if len(sys.argv) > 1 and sys.argv[1].lower() == "sandbox":
        import json

        subcmd = sys.argv[2].lower() if len(sys.argv) > 2 else "explain"
        if subcmd == "report":
            from jarvis.sandbox.inspector import get_backend_capability_report

            rep = get_backend_capability_report()
            console.print(
                Panel(
                    json.dumps(rep, indent=2),
                    title="[bold cyan]JARVIS Backend Capability Report[/bold cyan]",
                    border_style="cyan",
                )
            )
            return
        elif subcmd == "explain":
            from jarvis.sandbox.inspector import explain_sandbox_configuration

            exp = explain_sandbox_configuration()
            console.print(
                Panel(
                    json.dumps(exp, indent=2),
                    title="[bold cyan]JARVIS Sandbox Security Inspector (Explain)[/bold cyan]",
                    border_style="cyan",
                )
            )
            return

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
                user_msg = read_user_input(console)
            except (EOFError, KeyboardInterrupt):
                console.print("\n[yellow]Session interrupted by user.[/yellow]")
                break

            if not user_msg:
                continue

            if user_msg.lower() in ("exit", "quit", "q", "bye"):
                console.print("[dim]Closing session...[/dim]")
                break

            if user_msg.lower().startswith("sandbox "):
                parts = user_msg.lower().split()
                sub = parts[1] if len(parts) > 1 else "explain"
                import json

                if sub == "report":
                    from jarvis.sandbox.inspector import get_backend_capability_report

                    rep = get_backend_capability_report()
                    console.print(
                        Panel(
                            json.dumps(rep, indent=2),
                            title="[bold cyan]JARVIS Backend Capability Report[/bold cyan]",
                            border_style="cyan",
                        )
                    )
                    continue
                elif sub == "explain":
                    from jarvis.sandbox.inspector import explain_sandbox_configuration

                    exp = explain_sandbox_configuration()
                    console.print(
                        Panel(
                            json.dumps(exp, indent=2),
                            title="[bold cyan]JARVIS Sandbox Security Inspector (Explain)[/bold cyan]",
                            border_style="cyan",
                        )
                    )
                    continue

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
