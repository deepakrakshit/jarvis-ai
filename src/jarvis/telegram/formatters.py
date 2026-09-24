"""Message, card, and status formatters for native JARVIS Telegram interface."""

import re
from typing import Any, Dict, Optional


def redact_sensitive_data(text: str) -> str:
    """Mask sensitive tokens, credentials, and private keys with safe placeholders."""
    if not text:
        return text

    sanitized = text
    # Telegram bot token
    sanitized = re.sub(
        r"\b\d{8,12}:[A-Za-z0-9_-]{30,45}\b",
        "[REDACTED_BOT_TOKEN]",
        sanitized,
    )
    # Google API key
    sanitized = re.sub(
        r"\bAIza[0-9A-Za-z-_]{30,45}\b",
        "[REDACTED_API_KEY]",
        sanitized,
    )
    # Groq API key
    sanitized = re.sub(
        r"\bgsk_[a-zA-Z0-9]{30,}\b",
        "[REDACTED_GROQ_KEY]",
        sanitized,
    )
    # Bearer tokens
    sanitized = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9\-_\.=]{16,}\b",
        "Bearer [REDACTED_TOKEN]",
        sanitized,
    )
    # Private keys
    sanitized = re.sub(
        r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----",
        "[REDACTED_PRIVATE_KEY]",
        sanitized,
    )
    return sanitized


def format_task_status_card(
    task_id: str,
    status_indicator: str,
    title: str,
    detail: Optional[str] = None,
    intent: Optional[str] = None,
) -> str:
    """Format a consistent, single editable task status card."""
    lines = [f"{status_indicator} <b>{title}</b>", f"<b>Task:</b> <code>{task_id}</code>"]

    if intent:
        clean_intent = redact_sensitive_data(intent)
        lines.append(f"<b>Intent:</b> <i>{clean_intent}</i>")

    if detail:
        clean_detail = redact_sensitive_data(detail)
        lines.append(f"\n{clean_detail}")

    return "\n".join(lines)


def format_approval_request_card(
    ticket_id: str,
    capability: str,
    risk_tier: str,
    target_resource: str,
    risk_summary: str,
    arguments_summary: Optional[Dict[str, Any]] = None,
) -> str:
    """Format an interactive operator approval card."""
    clean_cap = redact_sensitive_data(capability)
    clean_res = redact_sensitive_data(target_resource)
    clean_summary = redact_sensitive_data(risk_summary)

    lines = [
        "⚠️ <b>APPROVAL REQUIRED</b>",
        f"<b>Ticket:</b> <code>{ticket_id}</code>",
        f"<b>Capability:</b> <code>{clean_cap}</code>",
        f"<b>Risk Tier:</b> <b>{risk_tier.upper()}</b>",
        f"<b>Resource:</b> <code>{clean_res}</code>",
        f"<b>Reason:</b> {clean_summary}",
    ]

    if arguments_summary:
        lines.append("\n<b>Arguments:</b>")
        for k, v in arguments_summary.items():
            clean_k = redact_sensitive_data(str(k))
            clean_v = redact_sensitive_data(str(v))
            lines.append(f"• <code>{clean_k}</code>: <code>{clean_v}</code>")

    lines.append("\n<i>Action will NOT execute until authorized.</i>")
    return "\n".join(lines)


def format_call_status_card(
    target: str,
    call_state: str,
    duration_seconds: Optional[int] = None,
    objective: Optional[str] = None,
    reply_preview: Optional[str] = None,
    summary: Optional[str] = None,
) -> str:
    """Format single editable VoIP call status card."""
    clean_target = redact_sensitive_data(target)
    state_upper = call_state.upper()

    icon = "📞"
    if state_upper in ("RINGING", "DIALING"):
        icon = "📲"
    elif state_upper in ("CONNECTED", "IN_PROGRESS"):
        icon = "🟢"
    elif state_upper in ("ENDED", "COMPLETED"):
        icon = "✅"
    elif state_upper in ("FAILED", "UNANSWERED", "BUSY"):
        icon = "🔴"

    dur_str = f" ({duration_seconds}s)" if duration_seconds is not None else ""
    lines = [
        f"{icon} <b>CALL STATUS: {state_upper}{dur_str}</b>",
        f"<b>Target:</b> <code>{clean_target}</code>",
    ]

    if objective:
        clean_obj = redact_sensitive_data(objective)
        lines.append(f"<b>Objective:</b> <i>{clean_obj}</i>")

    if reply_preview:
        clean_reply = redact_sensitive_data(reply_preview)
        lines.append(f'\n<b>Recipient Reply:</b>\n<i>"{clean_reply}"</i>')

    if summary:
        clean_summary = redact_sensitive_data(summary)
        lines.append(f"\n<b>Summary:</b>\n{clean_summary}")

    return "\n".join(lines)


def format_activity_card(
    task_id: str,
    status_icon: str,
    status_text: str,
    intent: Optional[str] = None,
    action_name: Optional[str] = None,
    detail: Optional[str] = None,
) -> str:
    """Format single editable task activity card tracking execution."""
    clean_task_id = redact_sensitive_data(task_id)
    lines = [
        f"{status_icon} <b>JARVIS ACTIVITY: {status_text.upper()}</b>",
        f"<b>Task:</b> <code>{clean_task_id}</code>",
    ]

    if intent:
        clean_intent = redact_sensitive_data(intent)
        lines.append(f"<b>Intent:</b> <i>{clean_intent}</i>")

    if action_name:
        clean_act = redact_sensitive_data(action_name)
        lines.append(f"<b>Action:</b> <code>{clean_act}</code>")

    if detail:
        clean_detail = redact_sensitive_data(detail)
        lines.append(f"\n{clean_detail}")

    return "\n".join(lines)


def format_system_status(
    telegram_online: bool,
    operator_count: int,
    bot_username: Optional[str] = None,
    live_model: Optional[str] = None,
    whatsapp_status: Optional[str] = None,
) -> str:
    """Format diagnostic /status response."""
    tg_icon = (
        f"🟢 Connected (@{bot_username})" if (telegram_online and bot_username) else "🟢 Connected"
    )
    pair_icon = (
        f"🟢 Authorized ({operator_count} operator)" if operator_count > 0 else "🟡 Unpaired"
    )
    model_line = f"<b>Cognitive Engine:</b> 🟢 {live_model}\n" if live_model else ""
    wa_line = (
        f"<b>WhatsApp Voice:</b> {whatsapp_status}\n"
        if whatsapp_status
        else "<b>WhatsApp Voice:</b> 🟢 Ready\n"
    )

    return (
        "<b>JARVIS NATIVE TELEGRAM STATUS</b>\n\n"
        f"<b>Telegram Bot:</b> {tg_icon}\n"
        f"<b>JARVIS Control Plane:</b> 🟢 Integrated\n"
        f"{model_line}"
        f"{wa_line}"
        f"<b>Operator Pairing:</b> {pair_icon}\n"
    )


def format_help_card() -> str:
    """Format structured /help command guidance card."""
    return (
        "<b>JARVIS REMOTE CONTROL CONSOLE</b>\n\n"
        "• <b>Natural Language:</b> Speak naturally to search files, open apps, or query PC state.\n"
        "• <b>File Delivery:</b> Request files (e.g. <i>'send my java pbl presentation'</i>) for direct transfer.\n"
        "• <b>WhatsApp Calling:</b> Instruct JARVIS to call any contact or number with conversational objectives.\n"
        "• <b>/screenshot:</b> Capture and receive current desktop screen display.\n"
        "• <b>/status:</b> Inspect live daemon status, paired operators, and engine connectivity.\n"
        "• <b>/unpair:</b> Revoke authorization credentials for this device.\n"
        "• <b>Approvals:</b> Interactive inline authorization buttons for privileged actions.\n"
        "• <b>Inbound Files:</b> Upload files or images to automatically save them to the workspace."
    )


def format_welcome_card(callsign: str, paired: bool = True) -> str:
    """Format structured /start welcome card."""
    if paired:
        return (
            "🤖 <b>JARVIS ONLINE</b>\n\n"
            f"Good day, {callsign}. Remote control session is active and authenticated.\n\n"
            "All PC capabilities—file search & delivery, desktop automation, web intelligence, "
            "and WhatsApp VoIP calling—are standing by.\n\n"
            "How may I be of service?"
        )
    return (
        "🔒 <b>ACCESS DENIED</b>\n\n"
        "This JARVIS instance is private and requires cryptographic pairing.\n"
        "If you are the owner, generate a pairing link from the host PC using "
        "<code>jarvis telegram pair</code>."
    )
