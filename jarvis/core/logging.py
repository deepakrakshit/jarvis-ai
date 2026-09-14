"""JARVIS Structured Logging and Telemetry Context.

Implements context-aware structured logging with correlation IDs and automated secret redaction.
"""

import contextvars
import logging
import re
import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, cast

import structlog
from structlog.types import EventDict, WrappedLogger

# Correlation Context Variables
ctx_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
ctx_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "session_id", default=None
)
ctx_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_id", default=None)
ctx_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("agent_id", default=None)
ctx_attempt_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "attempt_id", default=None
)
ctx_logical_effect_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "logical_effect_id", default=None
)

# Redaction patterns for secrets
SENSITIVE_KEY_NAMES = {
    "api_key",
    "apikey",
    "secret",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "gemini_api_key",
    "groq_api_key",
    "openrouter_api_key",
    "vault_encryption_key",
}

REGEX_PATTERNS = [
    re.compile(r"(AIzaSy[A-Za-z0-9_-]{33})"),  # Google API key
    re.compile(r"(gsk_[A-Za-z0-9]{48,})"),  # Groq API key
    re.compile(r"(sk-or-v1-[A-Za-z0-9]{64})"),  # OpenRouter API key
    re.compile(r"(Bearer\s+[A-Za-z0-9_\-\.]{15,})", re.IGNORECASE),  # Bearer Token
]


def redact_secrets_processor(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Processor to scrub API keys, tokens, and passwords from log records."""

    def _scrub_value(val: Any) -> Any:
        if isinstance(val, str):
            res = val
            for pattern in REGEX_PATTERNS:
                res = pattern.sub("[REDACTED]", res)
            return res
        if isinstance(val, dict):
            return {
                k: ("[REDACTED]" if str(k).lower() in SENSITIVE_KEY_NAMES else _scrub_value(v))
                for k, v in val.items()
            }
        if isinstance(val, list):
            return [_scrub_value(item) for item in val]
        return val

    return {
        k: ("[REDACTED]" if str(k).lower() in SENSITIVE_KEY_NAMES else _scrub_value(v))
        for k, v in event_dict.items()
    }


def add_correlation_ids(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Inject active correlation IDs from context variables into the event dict."""
    if req_id := ctx_request_id.get():
        event_dict["request_id"] = req_id
    if sess_id := ctx_session_id.get():
        event_dict["session_id"] = sess_id
    if t_id := ctx_task_id.get():
        event_dict["task_id"] = t_id
    if a_id := ctx_agent_id.get():
        event_dict["agent_id"] = a_id
    if att_id := ctx_attempt_id.get():
        event_dict["attempt_id"] = att_id
    if eff_id := ctx_logical_effect_id.get():
        event_dict["logical_effect_id"] = eff_id
    return event_dict


def setup_logging(log_level: str = "INFO", json_format: bool = False) -> None:
    """Configure structlog and standard logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        add_correlation_ids,
        redact_secrets_processor,
    ]

    renderer: structlog.types.Processor
    if json_format:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[renderer],
    )
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a configured structlog bound logger."""
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))


@contextmanager
def bind_correlation(
    request_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
    attempt_id: str | None = None,
    logical_effect_id: str | None = None,
) -> Generator[None, None, None]:
    """Context manager to bind correlation IDs to the current execution context."""
    tokens = []
    if request_id is not None:
        tokens.append((ctx_request_id, ctx_request_id.set(request_id)))
    if session_id is not None:
        tokens.append((ctx_session_id, ctx_session_id.set(session_id)))
    if task_id is not None:
        tokens.append((ctx_task_id, ctx_task_id.set(task_id)))
    if agent_id is not None:
        tokens.append((ctx_agent_id, ctx_agent_id.set(agent_id)))
    if attempt_id is not None:
        tokens.append((ctx_attempt_id, ctx_attempt_id.set(attempt_id)))
    if logical_effect_id is not None:
        tokens.append((ctx_logical_effect_id, ctx_logical_effect_id.set(logical_effect_id)))

    try:
        yield
    finally:
        for var, token in tokens:
            var.reset(token)
