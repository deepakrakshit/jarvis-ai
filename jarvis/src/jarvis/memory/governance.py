"""Memory Governance and Write Pipeline Security.

Enforces Section 26 invariants:
- Sensitive-data scanning and redaction (credentials, tokens, keys).
- Provenance enforcement (external untrusted content cannot silently become trusted user preferences).
- Trust tier assignment and quarantine protocols.
"""

import re
from typing import List, Tuple

from jarvis.contracts.memory import MemoryRecord, MemoryType, ProvenanceSource, TrustLevel
from jarvis.telemetry import logger

# Regex patterns for sensitive credential signatures
SECRET_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("Google API Key", re.compile(r"AIza[0-9A-Za-z\\-_]{35}")),
    ("Groq API Key", re.compile(r"gsk_[a-zA-Z0-9]{48,64}")),
    ("OpenAI API Key", re.compile(r"sk-[a-zA-Z0-9]{20,64}")),
    ("GitHub Token", re.compile(r"gh[pousr]-[A-Za-z0-9_]{36,255}")),
    ("Generic Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9\\-_.~+/]+=*", re.IGNORECASE)),
    ("Private Key Header", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
]

# Patterns for password/secret key-value fields
CREDENTIAL_KV_PATTERN = re.compile(
    r"(?i)\b(?:password|passwd|secret|api_key|access_token|private_key)\s*[:=]\s*['\"]?([^\s'\"]{6,})['\"]?"
)


class MemoryGovernance:
    """Security and compliance gatekeeper for durable memory writes."""

    def __init__(self, redact_secrets: bool = True) -> None:
        self.redact_secrets = redact_secrets

    def scan_and_redact_sensitive_content(self, text: str) -> Tuple[str, bool]:
        """Scan text for credentials and optionally redact them.

        Returns:
            Tuple of (sanitized_text, contains_secrets_flag)
        """
        if not text:
            return text, False

        sanitized = text
        found_secret = False

        for name, pattern in SECRET_PATTERNS:
            if pattern.search(sanitized):
                found_secret = True
                logger.warning(f"Memory governance detected sensitive credential: {name}")
                if self.redact_secrets:
                    sanitized = pattern.sub(
                        f"[REDACTED_{name.upper().replace(' ', '_')}]", sanitized
                    )

        if CREDENTIAL_KV_PATTERN.search(sanitized):
            found_secret = True
            logger.warning("Memory governance detected potential password or secret key-value pair")
            if self.redact_secrets:
                sanitized = CREDENTIAL_KV_PATTERN.sub(r"\1: [REDACTED_CREDENTIAL]", sanitized)

        return sanitized, found_secret

    def evaluate_write_admission(self, record: MemoryRecord) -> MemoryRecord:
        """Validate, sanitize, and assign appropriate trust level to a memory record.

        Enforces architectural rule: Untrusted sources cannot write trusted user preferences.
        """
        sanitized_content, has_secrets = self.scan_and_redact_sensitive_content(record.content)
        record.content = sanitized_content

        # Determine trust level and quarantine status
        if has_secrets and not self.redact_secrets:
            record.trust_level = TrustLevel.QUARANTINED
            logger.info(f"Memory record {record.record_id} quarantined due to unredacted secrets")
            return record

        untrusted_sources = {
            ProvenanceSource.WEB_PAGE,
            ProvenanceSource.DOCUMENT,
            ProvenanceSource.EMAIL,
            ProvenanceSource.MODEL_PROPOSAL,
        }

        # Prevent untrusted external content from spoofing operator preferences
        if record.provenance_source in untrusted_sources:
            if record.memory_type == MemoryType.USER_PREFERENCE:
                logger.warning(
                    f"Blocked untrusted source {record.provenance_source.value} "
                    f"from creating USER_PREFERENCE memory. Demoting to EPISODIC."
                )
                record.memory_type = MemoryType.EPISODIC

            record.trust_level = TrustLevel.UNTRUSTED_EXTERNAL
            # Cap initial importance of untrusted memories
            record.importance_score = min(record.importance_score, 0.4)
        elif record.provenance_source == ProvenanceSource.DIRECT_USER_INSTRUCTION:
            record.trust_level = TrustLevel.TRUSTED_OPERATOR
        elif record.provenance_source == ProvenanceSource.LOCAL_SYSTEM_FACT:
            record.trust_level = TrustLevel.VERIFIED_SYSTEM

        return record
