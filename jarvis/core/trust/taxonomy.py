"""JARVIS Input Trust Taxonomy and Hierarchy.

Defines the canonical trust levels and lattice operations for all data
entering or circulating within the system.
"""

from enum import StrEnum


class TrustLevel(StrEnum):
    """Explicit trust taxonomy from Layer 3 of JARVIS architecture.

    Order of authority (lowest to highest):
    EXTERNAL_UNTRUSTED < MODEL_GENERATED < ARTIFACT_INTEGRITY_VERIFIED <
    ARTIFACT_SEMANTICALLY_VERIFIED < USER_INPUT < SYSTEM_POLICY
    """

    SYSTEM_POLICY = "SYSTEM_POLICY"
    """Platform configuration, built-in policies, and signed security invariants."""

    USER_INPUT = "USER_INPUT"
    """Direct, authenticated user prompts and interactive UI inputs."""

    ARTIFACT_SEMANTICALLY_VERIFIED = "ARTIFACT_SEMANTICALLY_VERIFIED"
    """Data verified against external ground-truth systems via independent verifiers."""

    ARTIFACT_INTEGRITY_VERIFIED = "ARTIFACT_INTEGRITY_VERIFIED"
    """Data verified via byte-level cryptographic hashes against trusted manifests."""

    MODEL_GENERATED = "MODEL_GENERATED"
    """Proposed agent content, tool calls, and LLM completions (untrusted intent)."""

    EXTERNAL_UNTRUSTED = "EXTERNAL_UNTRUSTED"
    """External web fetches, emails, PDFs, user-uploaded files, and MCP tool outputs."""


# Trust rank: higher numerical value indicates higher authority
_TRUST_RANK: dict[TrustLevel, int] = {
    TrustLevel.EXTERNAL_UNTRUSTED: 0,
    TrustLevel.MODEL_GENERATED: 1,
    TrustLevel.ARTIFACT_INTEGRITY_VERIFIED: 2,
    TrustLevel.ARTIFACT_SEMANTICALLY_VERIFIED: 3,
    TrustLevel.USER_INPUT: 4,
    TrustLevel.SYSTEM_POLICY: 5,
}


def get_trust_rank(level: TrustLevel) -> int:
    """Return the numerical authority rank of a TrustLevel."""
    return _TRUST_RANK[level]


def is_trust_at_least(actual: TrustLevel, minimum: TrustLevel) -> bool:
    """Return True if actual trust level equals or exceeds the minimum required rank."""
    return _TRUST_RANK[actual] >= _TRUST_RANK[minimum]


def meet_trust_levels(*levels: TrustLevel) -> TrustLevel:
    """Compute the greatest lower bound (meet) of multiple trust levels.

    Conservative rule: Any untrusted or lower-trust input taints the aggregate.
    """
    if not levels:
        return TrustLevel.EXTERNAL_UNTRUSTED
    return min(levels, key=lambda lvl: _TRUST_RANK[lvl])
