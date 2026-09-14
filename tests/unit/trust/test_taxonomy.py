"""Tests for TrustLevel taxonomy and lattice meet/join operations."""

from jarvis.core.trust.taxonomy import (
    TrustLevel,
    get_trust_rank,
    is_trust_at_least,
    meet_trust_levels,
)


def test_trust_level_canonical_values() -> None:
    """Verify all 6 canonical trust levels from Layer 3 exist."""
    expected = {
        "SYSTEM_POLICY",
        "USER_INPUT",
        "ARTIFACT_SEMANTICALLY_VERIFIED",
        "ARTIFACT_INTEGRITY_VERIFIED",
        "MODEL_GENERATED",
        "EXTERNAL_UNTRUSTED",
    }
    assert {lvl.value for lvl in TrustLevel} == expected


def test_trust_hierarchy_ordering() -> None:
    """Verify strictly monotonic ranking from EXTERNAL_UNTRUSTED to SYSTEM_POLICY."""
    assert get_trust_rank(TrustLevel.EXTERNAL_UNTRUSTED) == 0
    assert get_trust_rank(TrustLevel.MODEL_GENERATED) == 1
    assert get_trust_rank(TrustLevel.ARTIFACT_INTEGRITY_VERIFIED) == 2
    assert get_trust_rank(TrustLevel.ARTIFACT_SEMANTICALLY_VERIFIED) == 3
    assert get_trust_rank(TrustLevel.USER_INPUT) == 4
    assert get_trust_rank(TrustLevel.SYSTEM_POLICY) == 5

    assert is_trust_at_least(TrustLevel.SYSTEM_POLICY, TrustLevel.USER_INPUT)
    assert is_trust_at_least(TrustLevel.USER_INPUT, TrustLevel.EXTERNAL_UNTRUSTED)
    assert not is_trust_at_least(TrustLevel.EXTERNAL_UNTRUSTED, TrustLevel.MODEL_GENERATED)


def test_meet_trust_levels_conservative_taint() -> None:
    """Verify that meet_trust_levels computes the lowest trust rank (taint propagation)."""
    # Combining SYSTEM_POLICY with EXTERNAL_UNTRUSTED must result in EXTERNAL_UNTRUSTED
    assert (
        meet_trust_levels(TrustLevel.SYSTEM_POLICY, TrustLevel.EXTERNAL_UNTRUSTED)
        == TrustLevel.EXTERNAL_UNTRUSTED
    )

    # Combining USER_INPUT with MODEL_GENERATED results in MODEL_GENERATED
    assert (
        meet_trust_levels(TrustLevel.USER_INPUT, TrustLevel.MODEL_GENERATED)
        == TrustLevel.MODEL_GENERATED
    )

    # Empty meet defaults safely to lowest rank
    assert meet_trust_levels() == TrustLevel.EXTERNAL_UNTRUSTED
