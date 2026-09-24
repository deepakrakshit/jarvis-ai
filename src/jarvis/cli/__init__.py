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
    handle_app_close,
    handle_app_focus,
    handle_app_inspect,
    handle_app_interact,
    handle_app_launch,
    handle_app_windows,
    handle_chat,
    handle_cron,
    handle_gateway,
    handle_run,
    handle_serve,
    handle_status,
    handle_telegram_daemon,
    handle_telegram_pair,
    handle_telegram_status,
    handle_telegram_unpair,
    handle_whatsapp_call,
    handle_whatsapp_history,
    handle_whatsapp_login,
    handle_whatsapp_logout,
    handle_whatsapp_status,
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

    # 8. app
    app_parser = subparsers.add_parser("app", help="Deep in-app and UI automation controls")
    app_sub = app_parser.add_subparsers(dest="app_subcommand", help="App subcommands")

    launch_p = app_sub.add_parser("launch", help="Launch an application asynchronously")
    launch_p.add_argument("target", type=str, help="Application name or executable path")
    launch_p.add_argument("arguments", nargs="*", help="Optional command line arguments")

    focus_p = app_sub.add_parser("focus", help="Focus an application window")
    focus_p.add_argument("target", type=str, help="Application name or window title")

    close_p = app_sub.add_parser("close", help="Close an application window")
    close_p.add_argument("target", type=str, help="Application name or window title")

    app_sub.add_parser("windows", help="List all open desktop application windows")

    inspect_p = app_sub.add_parser("inspect", help="Inspect window UI automation tree")
    inspect_p.add_argument("--window", type=str, default=None, help="Target window title")
    inspect_p.add_argument("--depth", type=int, default=5, help="Traversal depth")

    interact_p = app_sub.add_parser("interact", help="Interact with UI element")
    interact_p.add_argument("--window", type=str, required=True, help="Target window title")
    interact_p.add_argument("--query", type=str, required=True, help="Target element query")
    interact_p.add_argument(
        "--action", type=str, default="click", help="Action (click, set_value, toggle, etc.)"
    )
    interact_p.add_argument("--value", type=str, default=None, help="Value for set_value action")

    # 9. whatsapp
    wa_parser = subparsers.add_parser(
        "whatsapp", help="Autonomous WhatsApp VoIP calling operations"
    )
    wa_sub = wa_parser.add_subparsers(dest="whatsapp_subcommand", help="WhatsApp subcommands")

    wa_call_p = wa_sub.add_parser("call", help="Place an autonomous WhatsApp voice call")
    wa_call_p.add_argument(
        "--target",
        "-t",
        type=str,
        required=True,
        help="Contact name or phone number with country code",
    )
    wa_call_p.add_argument(
        "--objective",
        "-o",
        type=str,
        required=True,
        help="Call objective or message to deliver",
    )
    wa_call_p.add_argument(
        "--mode",
        "-m",
        type=str,
        default=None,
        choices=["MESSAGE_DELIVERY", "CONVERSATIONAL", "EMERGENCY"],
        help="Conversation mode (default: from configuration)",
    )
    wa_call_p.add_argument(
        "--duration",
        "-d",
        type=int,
        default=None,
        help="Max call duration in milliseconds (default: from configuration)",
    )
    wa_call_p.add_argument("--json", action="store_true", help="Output result as JSON")

    wa_status_p = wa_sub.add_parser(
        "status", help="Check WhatsApp VoIP calling engine and session status"
    )
    wa_status_p.add_argument("--json", action="store_true", help="Output status as JSON")

    wa_hist_p = wa_sub.add_parser("history", help="View recent WhatsApp call history")
    wa_hist_p.add_argument("--limit", "-n", type=int, default=5, help="Number of records to show")
    wa_hist_p.add_argument(
        "--query", "-q", type=str, default=None, help="Filter calls by search query"
    )
    wa_hist_p.add_argument("--json", action="store_true", help="Output history as JSON")

    wa_login_p = wa_sub.add_parser(
        "login", help="Authenticate WhatsApp via interactive QR code linking"
    )
    wa_login_p.add_argument(
        "--force", "-f", action="store_true", help="Force refresh existing session credentials"
    )
    wa_login_p.add_argument("--json", action="store_true", help="Output status as JSON")

    wa_logout_p = wa_sub.add_parser("logout", help="Log out and purge stored WhatsApp credentials")
    wa_logout_p.add_argument("--json", action="store_true", help="Output status as JSON")

    # 10. telegram
    tg_parser = subparsers.add_parser(
        "telegram", help="Native Telegram remote control daemon and pairing"
    )
    tg_sub = tg_parser.add_subparsers(dest="telegram_subcommand", help="Telegram subcommands")

    tg_status_p = tg_sub.add_parser("status", help="Show Telegram bot and pairing status")
    tg_status_p.add_argument("--json", action="store_true", help="Output status as JSON")

    tg_pair_p = tg_sub.add_parser("pair", help="Generate a secure single-use pairing link")
    tg_pair_p.add_argument("--ttl", type=int, default=300, help="Pairing link TTL in seconds")
    tg_pair_p.add_argument(
        "--no-wait",
        action="store_true",
        help="Print pairing link and exit without waiting for confirmation",
    )

    tg_sub.add_parser("unpair", help="Revoke all paired Telegram operators")
    tg_sub.add_parser("daemon", help="Run standalone Telegram bot daemon")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint invoked by 'jarvis' console script."""
    stdout_reconfig = getattr(sys.stdout, "reconfigure", None)
    if callable(stdout_reconfig):
        try:
            stdout_reconfig(encoding="utf-8", errors="replace")
        except Exception:
            pass
    stderr_reconfig = getattr(sys.stderr, "reconfigure", None)
    if callable(stderr_reconfig):
        try:
            stderr_reconfig(encoding="utf-8", errors="replace")
        except Exception:
            pass

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

        elif args.command == "app":
            if args.app_subcommand == "launch":
                res = handle_app_launch(target=args.target, arguments=args.arguments or None)
                print(json.dumps(res, indent=2))
            elif args.app_subcommand == "focus":
                res = handle_app_focus(target=args.target)
                print(json.dumps(res, indent=2))
            elif args.app_subcommand == "close":
                res = handle_app_close(target=args.target)
                print(json.dumps(res, indent=2))
            elif args.app_subcommand == "windows":
                win_list = handle_app_windows()
                print(json.dumps(win_list, indent=2))
            elif args.app_subcommand == "inspect":
                res = handle_app_inspect(window_target=args.window, max_depth=args.depth)
                print(json.dumps(res, indent=2))
            elif args.app_subcommand == "interact":
                res = handle_app_interact(
                    window_target=args.window,
                    element_query=args.query,
                    action=args.action,
                    value=args.value,
                )
                print(json.dumps(res, indent=2))
            else:
                parser.parse_args(["app", "--help"])
            return 0

        elif args.command == "whatsapp":
            subcmd = getattr(args, "whatsapp_subcommand", None)
            if subcmd == "call":
                call_res = asyncio.run(
                    handle_whatsapp_call(
                        target=args.target,
                        objective=args.objective,
                        mode=args.mode,
                        duration_ms=args.duration,
                    )
                )
                if getattr(args, "json", False):
                    print(json.dumps(call_res, indent=2))
                else:
                    if call_res["success"]:
                        print(f"Call Succeeded! Call ID: {call_res['call_id']}")
                        print(f"Target: {call_res['target_number']}")
                        print(f"Duration: {call_res['duration_seconds']}s")
                        if call_res.get("summary"):
                            print(f"Summary: {call_res['summary']}")
                        if call_res.get("recipient_reply"):
                            print(f"Recipient Reply: {call_res['recipient_reply']}")
                    else:
                        print(f"Call Failed: {call_res.get('error', 'Unknown error')}")
                return 0 if call_res["success"] else 1

            elif subcmd == "status":
                wa_stat = handle_whatsapp_status()
                if getattr(args, "json", False):
                    print(json.dumps(wa_stat, indent=2))
                else:
                    print(f"WhatsApp Engine Available: {wa_stat['available']}")
                    print(f"Authenticated Session: {wa_stat['authenticated']}")
                    print(f"Default Country Code: +{wa_stat['default_country_code']}")
                    print(f"Conversation Mode: {wa_stat['conversation_mode']}")
                    print(f"VoIP Enabled: {wa_stat['enabled']}")
                return 0

            elif subcmd == "history":
                records = handle_whatsapp_history(
                    limit=getattr(args, "limit", 5),
                    query=getattr(args, "query", None),
                )
                if getattr(args, "json", False):
                    print(json.dumps(records, indent=2))
                else:
                    if not records:
                        print("No call history found.")
                    else:
                        for idx, r in enumerate(records, 1):
                            print(
                                f"[{idx}] {r.get('target', 'Unknown')} "
                                f"({r.get('status', 'N/A')}) - {r.get('timestamp', '')}"
                            )
                            if r.get("summary"):
                                print(f"    Summary: {r['summary']}")
                            if r.get("recipientReply"):
                                print(f"    Reply: {r['recipientReply']}")
                return 0

            elif subcmd == "login":
                res = asyncio.run(
                    handle_whatsapp_login(force_refresh=getattr(args, "force", False))
                )
                if getattr(args, "json", False):
                    print(json.dumps(res, indent=2))
                else:
                    if res.get("success"):
                        print(
                            f"WhatsApp Authentication: {res.get('message', 'Device linked successfully.')}"
                        )
                    else:
                        print(
                            f"WhatsApp Authentication Failed: {res.get('error', 'Unknown error')}"
                        )
                return 0 if res.get("success") else 1

            elif subcmd == "logout":
                res = handle_whatsapp_logout()
                if getattr(args, "json", False):
                    print(json.dumps(res, indent=2))
                else:
                    print(res.get("message", "WhatsApp credentials cleared."))
                return 0

            else:
                parser.parse_args(["whatsapp", "--help"])
                return 0

        elif args.command == "telegram":
            subcmd = getattr(args, "telegram_subcommand", None)
            if subcmd == "status":
                status_res = handle_telegram_status()
                if getattr(args, "json", False):
                    print(json.dumps(status_res, indent=2))
                else:
                    print(f"Telegram Remote Bot Configured: {status_res['configured']}")
                    print(f"Telegram Bot Enabled: {status_res['enabled']}")
                    print(f"Authorized Operators Count: {status_res['authorized_operators_count']}")
                    if status_res["operators"]:
                        print("Operators:")
                        for op in status_res["operators"]:
                            print(
                                f"  - User ID: {op['telegram_user_id']}, Username: {op.get('username') or 'N/A'}"
                            )
                return 0

            elif subcmd == "pair":
                ttl = getattr(args, "ttl", 300)
                no_wait = getattr(args, "no_wait", False)

                def print_link(n: str, lk: str) -> None:
                    print("=== JARVIS TELEGRAM PAIRING CHALLENGE ===")
                    print(f"Pairing Code: {n}")
                    print(f"Pairing Link: {lk}")
                    print(f"This link expires in {ttl} seconds.")
                    print("\n📱 Open this link on your phone in Telegram and tap 'Start'.")
                    if not no_wait:
                        print(
                            "⏳ Listening for authorization from your phone... (Press Ctrl+C to cancel)\n"
                        )

                try:
                    nonce, link, paired_user = asyncio.run(
                        handle_telegram_pair(
                            ttl=ttl,
                            wait=not no_wait,
                            on_link_ready=print_link,
                        )
                    )
                    if not no_wait:
                        if paired_user:
                            uname = (
                                f"@{paired_user['username']}"
                                if paired_user.get("username")
                                else "Operator"
                            )
                            print(
                                f"🎉 Successfully paired with {uname} (ID: {paired_user['telegram_user_id']})!"
                            )
                            print(
                                "You can now start JARVIS by running 'run.bat' or 'python -m jarvis.cli serve'."
                            )
                        else:
                            print(
                                "\n⚠️ Pairing challenge expired before authorization was received."
                            )
                    return 0
                except (KeyboardInterrupt, asyncio.CancelledError):
                    print("\nPairing listening cancelled.")
                    return 0
                except Exception as err:
                    print(f"Pairing generation failed: {err}", file=sys.stderr)
                    return 1

            elif subcmd == "unpair":
                count = handle_telegram_unpair()
                print(f"Revoked authorization for {count} operator(s).")
                return 0

            elif subcmd == "daemon":
                asyncio.run(handle_telegram_daemon())
                return 0

            else:
                parser.parse_args(["telegram", "--help"])
                return 0

        else:
            parser.print_help()
            return 1

    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
