"""Unified CLI Entrypoint for JARVIS Personal AI Operating System.

Provides commands for status diagnostics, execution, gateway, cron, ACP, and unified serve.
"""

import argparse
import asyncio
import json
import sys
from typing import List, Optional

from jarvis.config import settings
from jarvis.contracts.task import TaskType

from .commands import (
    handle_acp,
    handle_chat,
    handle_cron,
    handle_gateway,
    handle_run,
    handle_serve,
    handle_status,
)


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser and subcommands."""
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="JARVIS - Stateful Personal AI Operating System",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"JARVIS OS v{settings.APP_VERSION}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # 1. status
    status_parser = subparsers.add_parser(
        "status", help="Show system status and health diagnostics"
    )
    status_parser.add_argument("--json", action="store_true", help="Output status as raw JSON")

    # 2. run
    run_parser = subparsers.add_parser(
        "run", help="Submit and execute a task through the Control Plane"
    )
    run_parser.add_argument("intent", type=str, help="Natural language intent to execute")
    run_parser.add_argument(
        "--type",
        type=str,
        default="WINDOWS_CONTROL",
        choices=[
            "WINDOWS_CONTROL",
            "CONVERSATION",
            "BROWSER",
            "DEEP_REASONING",
            "AGENT_DELEGATION",
        ],
        help="Task capability type",
    )

    # 3. gateway
    gw_parser = subparsers.add_parser("gateway", help="Start the WebSocket Gateway daemon")
    gw_parser.add_argument("--host", type=str, default=settings.GATEWAY_HOST, help="Gateway host")
    gw_parser.add_argument("--port", type=int, default=settings.GATEWAY_PORT, help="Gateway port")

    # 4. cron
    cron_parser = subparsers.add_parser("cron", help="Run heartbeat pulse or periodic scheduler")
    cron_parser.add_argument(
        "--once", action="store_true", default=True, help="Execute single pulse and exit"
    )
    cron_parser.add_argument(
        "--continuous", action="store_true", help="Run continuous periodic monitor"
    )
    cron_parser.add_argument(
        "--interval", type=float, default=60.0, help="Interval in seconds for continuous mode"
    )

    # 5. acp
    acp_parser = subparsers.add_parser(
        "acp", help="Agent Control Protocol external coding agent harness"
    )
    acp_sub = acp_parser.add_subparsers(dest="acp_subcommand", help="ACP subcommands")
    spawn_p = acp_sub.add_parser("spawn", help="Initialize a sandboxed ACP session")
    spawn_p.add_argument("--repo", type=str, required=True, help="Path to repository")
    spawn_p.add_argument("--model", type=str, default="GPT-OSS 120B", help="Model family")

    acp_run_p = acp_sub.add_parser("run", help="Run a turn in an ACP session")
    acp_run_p.add_argument("--repo", type=str, required=True, help="Path to repository")
    acp_run_p.add_argument("--instruction", type=str, required=True, help="Instruction prompt")
    acp_run_p.add_argument("--model", type=str, default="GPT-OSS 120B", help="Model family")

    # 6. serve
    serve_parser = subparsers.add_parser("serve", help="Run unified server (Gateway + Heartbeat)")
    serve_parser.add_argument("--host", type=str, default=settings.GATEWAY_HOST, help="Server host")
    serve_parser.add_argument("--port", type=int, default=settings.GATEWAY_PORT, help="Server port")
    serve_parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=60.0,
        help="Heartbeat monitor interval in seconds",
    )

    # 7. chat
    chat_parser = subparsers.add_parser(
        "chat", help="Start real-time interactive dialogue session with JARVIS"
    )
    chat_parser.add_argument(
        "--session-id", type=str, default=None, help="Session ID for conversation history"
    )
    chat_parser.add_argument(
        "--live", action="store_true", help="Connect to Gemini 3.8 Live bidirectional audio stream"
    )
    chat_parser.add_argument(
        "--message", type=str, default=None, help="Single-turn query to execute immediately"
    )
    chat_parser.add_argument(
        "--with-daemon",
        action="store_true",
        help="Start background daemon (Gateway + Heartbeat) alongside chat session",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint invoked by 'jarvis' console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        if args.command == "status":
            report = handle_status()
            if getattr(args, "json", False):
                print(json.dumps(report, indent=2))
            else:
                print(f"JARVIS OS v{report['version']} - Status: {report['status']}")
                print(f"Environment: {report['environment']}")
                print(f"Database: {report['database_path']}")
                print("Database Counts:")
                for k, v in report["database_stats"].items():
                    print(f"  - {k}: {v}")
                print(f"Host OS: {report['system_info'].get('os', 'Unknown')}")
            return 0

        elif args.command == "run":
            task_type = TaskType(args.type)
            result = asyncio.run(handle_run(intent=args.intent, task_type=task_type))
            print(f"Task {result['task_id']} finished with status: {result['state']}")
            if result.get("result_summary"):
                print(f"Result: {result['result_summary']}")
            if result.get("error_message"):
                print(f"Error: {result['error_message']}")
            return 0 if result["state"] == "COMPLETED" else 1

        elif args.command == "gateway":
            asyncio.run(handle_gateway(host=args.host, port=args.port))
            return 0

        elif args.command == "cron":
            once = not getattr(args, "continuous", False)
            results = asyncio.run(handle_cron(once=once, interval_seconds=args.interval))
            print(f"Executed heartbeat pulse: {json.dumps(results, indent=2)}")
            return 0

        elif args.command == "acp":
            subcmd = getattr(args, "acp_subcommand", None)
            if not subcmd:
                print("Error: Specify an ACP subcommand: 'spawn' or 'run'")
                return 1
            res = asyncio.run(
                handle_acp(
                    subcommand=subcmd,
                    repo_path=getattr(args, "repo", None),
                    model=getattr(args, "model", "GPT-OSS 120B"),
                    instruction=getattr(args, "instruction", None),
                )
            )
            print(json.dumps(res, indent=2))
            return 0

        elif args.command == "serve":
            asyncio.run(
                handle_serve(
                    host=args.host,
                    port=args.port,
                    heartbeat_interval=args.heartbeat_interval,
                )
            )
            return 0

        elif args.command == "chat":
            asyncio.run(
                handle_chat(
                    session_id=args.session_id,
                    live_mode=args.live,
                    message=args.message,
                    with_daemon=args.with_daemon,
                )
            )
            return 0

        else:
            parser.print_help()
            return 1

    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
