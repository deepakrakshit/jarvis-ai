"""Tests for FIDES IntegrityLabel and ConfidentialityLabel lattice operations."""

from jarvis.core.ifc.labels import (
    ConfidentialityLabel,
    IntegrityLabel,
    join_confidentiality_labels,
    meet_integrity_labels,
)


def test_integrity_labels_ranking_and_meet() -> None:
    """Verify IntegrityLabel lattice: UNTRUSTED < USER_CONTROLLED < SYSTEM_TRUSTED."""
    assert IntegrityLabel.UNTRUSTED.rank == 0
    assert IntegrityLabel.USER_CONTROLLED.rank == 1
    assert IntegrityLabel.SYSTEM_TRUSTED.rank == 2

    assert IntegrityLabel.SYSTEM_TRUSTED.is_at_least(IntegrityLabel.USER_CONTROLLED)
    assert not IntegrityLabel.UNTRUSTED.is_at_least(IntegrityLabel.USER_CONTROLLED)

    # Meet (greatest lower bound / taint)
    assert IntegrityLabel.SYSTEM_TRUSTED.meet(IntegrityLabel.UNTRUSTED) == IntegrityLabel.UNTRUSTED
    assert (
        IntegrityLabel.USER_CONTROLLED.meet(IntegrityLabel.SYSTEM_TRUSTED)
        == IntegrityLabel.USER_CONTROLLED
    )


def test_confidentiality_labels_ranking_and_join() -> None:
    """Verify ConfidentialityLabel lattice: PUBLIC < INTERNAL < CONFIDENTIAL < SECRET."""
    assert ConfidentialityLabel.PUBLIC.rank == 0
    assert ConfidentialityLabel.INTERNAL.rank == 1
    assert ConfidentialityLabel.CONFIDENTIAL.rank == 2
    assert ConfidentialityLabel.SECRET.rank == 3

    assert ConfidentialityLabel.PUBLIC.is_at_most(ConfidentialityLabel.INTERNAL)
    assert ConfidentialityLabel.SECRET.is_at_most(ConfidentialityLabel.SECRET)
    assert not ConfidentialityLabel.CONFIDENTIAL.is_at_most(ConfidentialityLabel.INTERNAL)

    # Join (least upper bound / sensitivity escalation)
    assert (
        ConfidentialityLabel.PUBLIC.join_label(ConfidentialityLabel.SECRET)
        == ConfidentialityLabel.SECRET
    )
    assert (
        ConfidentialityLabel.INTERNAL.escalate(ConfidentialityLabel.CONFIDENTIAL)
        == ConfidentialityLabel.CONFIDENTIAL
    )


def test_multi_label_meet_and_join_aggregates() -> None:
    """Verify aggregate meet/join across lists."""
    # Integrity meet
    assert (
        meet_integrity_labels(
            IntegrityLabel.SYSTEM_TRUSTED,
            IntegrityLabel.USER_CONTROLLED,
            IntegrityLabel.UNTRUSTED,
        )
        == IntegrityLabel.UNTRUSTED
    )
    assert meet_integrity_labels() == IntegrityLabel.UNTRUSTED

    # Confidentiality join
    assert (
        join_confidentiality_labels(
            ConfidentialityLabel.PUBLIC,
            ConfidentialityLabel.INTERNAL,
            ConfidentialityLabel.SECRET,
        )
        == ConfidentialityLabel.SECRET
    )
    assert join_confidentiality_labels() == ConfidentialityLabel.PUBLIC
