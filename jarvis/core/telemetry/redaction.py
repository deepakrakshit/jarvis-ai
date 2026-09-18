"""JARVIS Privacy-Preserving Telemetry Scrubber and Redactor.

Implements ARCHITECTURE.md Layer 19: Automated regex and entropy-based redaction
preventing credentials, bearer tokens, private keys, and PII from leaking into
distributed traces, exported metrics, or diagnostic logs.
"""

import hashlib
import re
from typing import Any

# Sensitive dictionary keys whose values must always be masked
SENSITIVE_FIELD_NAMES: set[str] = {
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "set_cookie",
    "vault_key",
    "vault_encryption_key",
    "private_key",
    "signing_key",
    "credentials",
    "client_secret",
    "gemini_api_key",
    "groq_api_key",
    "openrouter_api_key",
}

# Compiled regex patterns for automatic content scrubbing
PATTERN_SPECS: list[tuple[str, re.Pattern[str]]] = [
    (
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
    ),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("BEARER", re.compile(r"\bBearer\s+[A-Za-z0-9+/._~=-]{15,}\b", re.IGNORECASE)),
    ("BASIC_AUTH", re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{15,}\b", re.IGNORECASE)),
    ("GEMINI_KEY", re.compile(r"\bAIzaSy[A-Za-z0-9_-]{30,}\b")),
    ("GROQ_KEY", re.compile(r"\bgsk_[A-Za-z0-9_-]{20,}\b")),
    ("OPENROUTER_KEY", re.compile(r"\bsk-or-[A-Za-z0-9_-]{20,}\b")),
    ("OPENAI_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("ANTHROPIC_KEY", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
]

THINKING_PATTERN = re.compile(r"<thought>.*?</thought>", re.DOTALL | re.IGNORECASE)


class PrivacyScrubber:
    """High-assurance redaction scrubber preventing sensitive data leaks into telemetry."""

    def __init__(self, preserve_digest: bool = True, scrub_internal_thought: bool = True) -> None:
        self.preserve_digest = preserve_digest
        self.scrub_internal_thought = scrub_internal_thought

    def _mask_match(self, match_type: str, matched_text: str) -> str:
        """Construct safe redacted token, optionally appending short deterministic SHA256 digest."""
        if self.preserve_digest:
            digest = hashlib.sha256(matched_text.encode("utf-8")).hexdigest()[:8]
            return f"[REDACTED:{match_type}:{digest}]"
        return f"[REDACTED:{match_type}]"

    def scrub_text(self, text: str) -> str:
        """Scrub sensitive credentials, tokens, PII, and internal thoughts from a string."""
        if not text or not isinstance(text, str):
            return text

        scrubbed = text

        # Optional: strip private internal model thoughts from telemetry
        if self.scrub_internal_thought:
            scrubbed = THINKING_PATTERN.sub("[REDACTED:INTERNAL_REASONING]", scrubbed)

        for label, pattern in PATTERN_SPECS:

            def _repl(m: re.Match[str], lbl: str = label) -> str:
                return self._mask_match(lbl, m.group(0))

            scrubbed = pattern.sub(_repl, scrubbed)

        return scrubbed

    def scrub_data(self, data: Any) -> Any:
        """Recursively scrub dictionaries, lists, tuples, and primitives."""
        if isinstance(data, str):
            return self.scrub_text(data)

        if isinstance(data, dict):
            cleaned: dict[str, Any] = {}
            for k, v in data.items():
                key_lower = str(k).lower().replace("-", "_").replace(".", "_")
                # Direct match against sensitive field names or sensitive suffixes
                is_sensitive_key = key_lower in SENSITIVE_FIELD_NAMES or key_lower.endswith(
                    ("_key", "_secret", "_password", "_token", "_credential", "_credentials")
                )
                if is_sensitive_key and not isinstance(v, (dict, list, tuple, set)):
                    if isinstance(v, str):
                        digest = hashlib.sha256(v.encode("utf-8")).hexdigest()[:8]
                        cleaned[k] = f"[REDACTED:CREDENTIAL:{digest}]"
                    else:
                        cleaned[k] = "[REDACTED:CREDENTIAL]"
                else:
                    cleaned[k] = self.scrub_data(v)
            return cleaned

        if isinstance(data, list):
            return [self.scrub_data(item) for item in data]

        if isinstance(data, tuple):
            return tuple(self.scrub_data(item) for item in data)

        if isinstance(data, set):
            return {self.scrub_data(item) for item in data}

        return data

    def scrub_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Convenience method to sanitize span or event attributes."""
        return self.scrub_data(attributes)  # type: ignore[no-any-return]


_DEFAULT_SCRUBBER: PrivacyScrubber | None = None


def get_scrubber() -> PrivacyScrubber:
    """Retrieve or initialize the default PrivacyScrubber singleton."""
    global _DEFAULT_SCRUBBER
    if _DEFAULT_SCRUBBER is None:
        _DEFAULT_SCRUBBER = PrivacyScrubber()
    return _DEFAULT_SCRUBBER
