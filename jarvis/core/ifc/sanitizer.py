"""JARVIS Content Sanitization, Secret Redaction, and Injection Detection.

Provides heuristic scanning for prompt injection signatures and secret scrubbing
to prevent data leakage across IFC trust boundaries.
"""

import re

from jarvis.core.exceptions import PromptInjectionDetectedError
from jarvis.core.ifc.labels import ConfidentialityLabel
from jarvis.core.ifc.taint import LabeledData
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Heuristic prompt injection patterns
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"ignore\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions?|directives?|rules?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"you\s+are\s+now\s+(?:in\s+)?(?:dan|developer\s+mode|unrestricted|jailbreak)",
        re.IGNORECASE,
    ),
    re.compile(r"<\s*\|?\s*im_start\s*\|?\s*>\s*system", re.IGNORECASE),
    re.compile(r"\[\s*system\s+(?:message|prompt|override|directive)?\s*\]", re.IGNORECASE),
    re.compile(r"\[\s*system\s*\]", re.IGNORECASE),
    re.compile(r"(?:new\s+)?system\s+directive\s*:", re.IGNORECASE),
    re.compile(
        r"disregard\s+(?:all\s+)?(?:your\s+)?(?:guidelines?|instructions?|rules?|user\s+prompts?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:reveal|exfiltrate|dump|leak)\s+(?:all\s+)?(?:secrets?|api[_\s-]?keys?|passwords?|tokens?|environment\s+variables?)",
        re.IGNORECASE,
    ),
]

_SECRET_PATTERN = re.compile(
    r"(?i)(?:key|token|secret|password|bearer|auth)[=:\s]+(['\"]?)([A-Za-z0-9_\-\.]{16,})\1"
)


class ContentSanitizer:
    """Heuristic scanner and sanitization engine."""

    @classmethod
    def scan_for_injection(cls, text: str, strict: bool = False) -> list[str]:
        """Scan text for known prompt injection and instruction hijack signatures.

        Returns list of matched patterns. Raises PromptInjectionDetectedError if strict=True.
        """
        matches: list[str] = []
        for pattern in _INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                matches.append(match.group(0))

        if matches:
            logger.warning(
                "prompt_injection_pattern_detected",
                matched_patterns=matches,
                sample=text[:120],
            )
            if strict:
                raise PromptInjectionDetectedError(
                    f"Prompt injection detected in input: {matches[0]}"
                )

        return matches

    @classmethod
    def redact_secrets(cls, text: str) -> str:
        """Mask potential secrets, API keys, and auth tokens with [REDACTED]."""
        return _SECRET_PATTERN.sub(r"key=[REDACTED]", text)

    @classmethod
    def sanitize_to_public(
        cls,
        data: LabeledData[str],
        transform_description: str = "redact_secrets_to_public",
    ) -> LabeledData[str]:
        """Redact secrets and lower confidentiality to PUBLIC via authorized sanitizer."""
        cleaned_text = cls.redact_secrets(data.data)
        # Derive with new raw data and authorized declassification
        new_prov = data.provenance.derive(
            parents=[data.provenance],
            transform_description=transform_description,
            raw_data=cleaned_text,
        )
        # Explicit authorized declassification
        object.__setattr__(new_prov, "confidentiality_label", ConfidentialityLabel.PUBLIC)
        return LabeledData[str](data=cleaned_text, provenance=new_prov)
