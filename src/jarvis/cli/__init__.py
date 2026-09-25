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
    handle_whatsapp_chats_list,
    handle_whatsapp_contacts_alias,
    handle_whatsapp_contacts_check,
    handle_whatsapp_contacts_get,
    handle_whatsapp_contacts_search,
    handle_whatsapp_contacts_tags,
    handle_whatsapp_history,
    handle_whatsapp_login,
    handle_whatsapp_logout,
    handle_whatsapp_messages_context,
    handle_whatsapp_messages_list,
    handle_whatsapp_messages_search,
    handle_whatsapp_send,
    handle_whatsapp_status,
    handle_whatsapp_sync,
    handle_whatsapp_unread,
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

    wa_sync_p = wa_sub.add_parser(
        "sync", help="Synchronize chats, contacts, and messages from WhatsApp"
    )
    wa_sync_p.add_argument("--full", action="store_true", help="Perform full historical sync")
    wa_sync_p.add_argument("--json", action="store_true", help="Output result as JSON")

    wa_contacts_p = wa_sub.add_parser(
        "contacts", help="Search, inspect, and manage WhatsApp contacts"
    )
    wa_contacts_p.add_argument(
        "query", nargs="?", default="", help="Search query (name, phone, alias)"
    )
    wa_contacts_p.add_argument("--limit", "-n", type=int, default=20, help="Max results")
    wa_contacts_p.add_argument("--alias", type=str, default=None, help="Set alias for contact")
    wa_contacts_p.add_argument("--remove-alias", action="store_true", help="Remove specified alias")
    wa_contacts_p.add_argument("--tag", type=str, default=None, help="Add tag to contact")
    wa_contacts_p.add_argument("--remove-tag", action="store_true", help="Remove tag from contact")
    wa_contacts_p.add_argument(
        "--check", type=str, default=None, help="Check phone number on WhatsApp"
    )
    wa_contacts_p.add_argument(
        "--get", action="store_true", help="Resolve specific contact and show full details"
    )
    wa_contacts_p.add_argument("--json", action="store_true", help="Output as JSON")

    wa_chats_p = wa_sub.add_parser("chats", help="List WhatsApp chats")
    wa_chats_p.add_argument("--limit", "-n", type=int, default=50, help="Max chats to list")
    wa_chats_p.add_argument("--unread", action="store_true", help="Filter unread chats only")
    wa_chats_p.add_argument("--archived", action="store_true", help="Include archived chats")
    wa_chats_p.add_argument("--json", action="store_true", help="Output as JSON")

    wa_messages_p = wa_sub.add_parser("messages", help="List, search, and view WhatsApp messages")
    wa_messages_p.add_argument("query", nargs="?", default="", help="Search text query")
    wa_messages_p.add_argument(
        "--chat", "-c", type=str, default=None, help="Chat JID or contact name"
    )
    wa_messages_p.add_argument("--limit", "-n", type=int, default=20, help="Max messages to list")
    wa_messages_p.add_argument(
        "--context", type=str, default=None, help="Message ID to view context for"
    )
    wa_messages_p.add_argument("--radius", type=int, default=5, help="Context radius")
    wa_messages_p.add_argument("--json", action="store_true", help="Output as JSON")

    wa_unread_p = wa_sub.add_parser(
        "unread", help="Review unread WhatsApp messages and generate proposed draft replies"
    )
    wa_unread_p.add_argument("--limit", "-n", type=int, default=15, help="Max chats to inspect")
    wa_unread_p.add_argument("--context", type=int, default=5, help="Context messages per chat")
    wa_unread_p.add_argument("--json", action="store_true", help="Output as JSON")

    wa_send_p = wa_sub.add_parser(
        "send", help="Send WhatsApp messages, files, voices, or reactions"
    )
    wa_send_p.add_argument(
        "--to", "-t", type=str, required=True, help="Recipient name, phone, or JID"
    )
    wa_send_p.add_argument("--message", "-m", type=str, default=None, help="Text message content")
    wa_send_p.add_argument("--file", "-f", type=str, default=None, help="File path to send")
    wa_send_p.add_argument("--caption", "-c", type=str, default=None, help="Caption for file")
    wa_send_p.add_argument(
        "--voice", "-v", type=str, default=None, help="Audio file path to send as voice note"
    )
    wa_send_p.add_argument("--react", "-r", type=str, default=None, help="Reaction emoji")
    wa_send_p.add_argument("--msg-id", type=str, default=None, help="Message ID for reaction")
    wa_send_p.add_argument("--json", action="store_true", help="Output as JSON")

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

            elif subcmd == "sync":
                res = asyncio.run(handle_whatsapp_sync(full=getattr(args, "full", False)))
                if getattr(args, "json", False):
                    print(json.dumps(res, indent=2))
                else:
                    if res.get("success"):
                        stats = res.get("store_stats", {})
                        print("WhatsApp Mirror Sync Completed Successfully!")
                        print(
                            f"Contacts: {stats.get('contacts', 0)} | Chats: {stats.get('chats', 0)} | "
                            f"Messages: {stats.get('messages', 0)} | Calls: {stats.get('calls', 0)}"
                        )
                    else:
                        print(f"WhatsApp Sync Failed: {res.get('error', 'Unknown error')}")
                return 0 if res.get("success") else 1

            elif subcmd == "contacts":
                if getattr(args, "check", None):
                    res = asyncio.run(handle_whatsapp_contacts_check(args.check))
                    if getattr(args, "json", False):
                        print(json.dumps(res, indent=2))
                    else:
                        status = (
                            "Registered on WhatsApp"
                            if res.get("registered")
                            else "Not registered on WhatsApp"
                        )
                        print(f"{args.check}: {status} (JID: {res.get('jid', 'N/A')})")
                    return 0
                if getattr(args, "alias", None):
                    res = handle_whatsapp_contacts_alias(
                        jid=getattr(args, "query", ""),
                        alias=args.alias,
                        remove=getattr(args, "remove_alias", False),
                    )
                    if getattr(args, "json", False):
                        print(json.dumps(res, indent=2))
                    else:
                        print(f"Alias '{args.alias}' updated successfully.")
                    return 0
                if getattr(args, "tag", None):
                    res = handle_whatsapp_contacts_tags(
                        jid=getattr(args, "query", ""),
                        tag=args.tag,
                        remove=getattr(args, "remove_tag", False),
                    )
                    if getattr(args, "json", False):
                        print(json.dumps(res, indent=2))
                    else:
                        print(f"Tag '{args.tag}' updated successfully.")
                    return 0

                query = getattr(args, "query", "")
                if getattr(args, "get", False):
                    res = handle_whatsapp_contacts_get(query)
                    if getattr(args, "json", False):
                        print(json.dumps(res, indent=2))
                    else:
                        if res.get("resolved"):
                            print(
                                f"Contact: {res.get('display_name')} (JID: {res.get('canonical_jid')})"
                            )
                        elif res.get("ambiguous"):
                            print(f"Ambiguous: {res.get('disambiguation_prompt')}")
                        else:
                            print(f"Not found: {res.get('error')}")
                    return 0

                contacts = handle_whatsapp_contacts_search(
                    query=query, limit=getattr(args, "limit", 20)
                )
                if getattr(args, "json", False):
                    print(json.dumps(contacts, indent=2))
                else:
                    if not contacts:
                        print(f"No contacts found matching '{query}'.")
                    else:
                        print(f"Found {len(contacts)} contact(s):")
                        for idx, c in enumerate(contacts, 1):
                            alias_str = f" [alias: {c['alias']}]" if c.get("alias") else ""
                            phone_str = f" ({c['phone']})" if c.get("phone") else ""
                            tags_str = f" tags: {','.join(c['tags'])}" if c.get("tags") else ""
                            print(
                                f"[{idx}] {c['display_name']}{phone_str}{alias_str} - {c['jid']}{tags_str}"
                            )
                return 0

            elif subcmd == "chats":
                chats = handle_whatsapp_chats_list(
                    limit=getattr(args, "limit", 50),
                    unread_only=getattr(args, "unread", False),
                )
                if getattr(args, "json", False):
                    print(json.dumps(chats, indent=2))
                else:
                    if not chats:
                        print("No chats found.")
                    else:
                        print(f"Chats ({len(chats)}):")
                        for idx, ch in enumerate(chats, 1):
                            unread_badge = (
                                f" [UNREAD: {ch['unread_count']}]" if ch.get("unread_count") else ""
                            )
                            print(
                                f"[{idx}] {ch.get('name') or ch['jid']} ({ch['kind']}){unread_badge} - {ch['jid']}"
                            )
                return 0

            elif subcmd == "messages":
                context_id = getattr(args, "context", None)
                chat_jid = getattr(args, "chat", None)
                if context_id and chat_jid:
                    messages = handle_whatsapp_messages_context(
                        chat_jid=chat_jid,
                        message_id=context_id,
                        radius=getattr(args, "radius", 5),
                    )
                elif getattr(args, "query", ""):
                    messages = handle_whatsapp_messages_search(
                        query=args.query,
                        chat_jid=chat_jid,
                        limit=getattr(args, "limit", 20),
                    )
                elif chat_jid:
                    messages = handle_whatsapp_messages_list(
                        chat_jid=chat_jid,
                        limit=getattr(args, "limit", 50),
                    )
                else:
                    messages = handle_whatsapp_messages_search(
                        query="",
                        limit=getattr(args, "limit", 20),
                    )

                if getattr(args, "json", False):
                    print(json.dumps(messages, indent=2))
                else:
                    if not messages:
                        print("No messages found.")
                    else:
                        for m in messages:
                            sender = (
                                "Me" if m.get("from_me") else (m.get("sender_name") or "Remote")
                            )
                            print(f"[{m.get('msg_id', '')[:8]}] {sender}: {m.get('text', '')}")
                return 0

            elif subcmd == "unread":
                res = asyncio.run(
                    handle_whatsapp_unread(
                        limit=getattr(args, "limit", 15),
                        context=getattr(args, "context", 5),
                    )
                )
                if getattr(args, "json", False):
                    print(json.dumps(res, indent=2))
                else:
                    reviews = res.get("reviews", [])
                    print(f"Unread Conversations Review ({len(reviews)} active):")
                    for r in reviews:
                        print(f"\n--- Chat: {r['display_name']} ({r['unread_count']} unread) ---")
                        print(f"Summary: {r['conversation_summary']}")
                        print(f"Intent: {r['intent']}")
                        if r.get("requires_reply"):
                            print(f'PROPOSED DRAFT: "{r.get("draft")}"')
                        else:
                            print("Requires Reply: No")
                    print(
                        "\n[Safety Guarantee: Drafts held in memory. Nothing was sent automatically.]"
                    )
                return 0

            elif subcmd == "send":
                res = asyncio.run(
                    handle_whatsapp_send(
                        to=args.to,
                        message=getattr(args, "message", None),
                        file_path=getattr(args, "file", None),
                        caption=getattr(args, "caption", None),
                        voice_path=getattr(args, "voice", None),
                        react=getattr(args, "react", None),
                        msg_id=getattr(args, "msg_id", None),
                    )
                )
                if getattr(args, "json", False):
                    print(json.dumps(res, indent=2))
                else:
                    if res.get("success"):
                        print(f"WhatsApp Message Dispatched Successfully! (Target: {args.to})")
                    else:
                        print(f"Send Failed: {res.get('error', 'Unknown error')}")
                        if res.get("disambiguation_prompt"):
                            print(f"Disambiguation: {res['disambiguation_prompt']}")
                return 0 if res.get("success") else 1

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
