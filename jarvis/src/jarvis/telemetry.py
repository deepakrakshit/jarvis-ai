"""Telemetry, Structured Logging, and Secret Masking for JARVIS.

Core Invariant: Keep credentials, private data/config, and secrets out
of log streams and persistent transcripts.
"""

import logging
import re
import sys

from jarvis.config import settings

# Regex patterns for masking sensitive strings
SECRET_PATTERNS = [
    re.compile(r"(AIzaSy[A-Za-z0-9_-]{33})"),  # Google API keys
    re.compile(r"(AQ\.[A-Za-z0-9_-]{40,})"),  # Gemini Live auth tokens
    re.compile(r"(gsk_[A-Za-z0-9]{40,})"),  # Groq API keys
    re.compile(r"(sk-[A-Za-z0-9]{32,})"),  # Standard OpenAI style keys
]


def mask_secrets(text: str) -> str:
    """Mask any detected API credentials or sensitive tokens."""
    if not text:
        return text
    masked = text
    for pattern in SECRET_PATTERNS:
        masked = pattern.sub(r"[REDACTED_SECRET]", masked)
    return masked


class SafeFormatter(logging.Formatter):
    """Custom logging formatter that strips API keys and formats timestamps."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return mask_secrets(original)


def setup_logger(name: str = "jarvis") -> logging.Logger:
    """Configure and return the root or child JARVIS logger."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    settings.ensure_directories()

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = SafeFormatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File Handler
    log_file = settings.LOG_DIR / "jarvis.log"
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = SafeFormatter(
        "%(asctime)s [%(levelname)s] [%(name)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger("jarvis")
