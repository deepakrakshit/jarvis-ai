"""Tests for ElevationPolicy and the Non-Elevation Axiom."""

import pytest

from jarvis.core.exceptions import TrustElevationError
from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.ifc.rules import ElevationPolicy
from jarvis.core.trust.taxonomy import TrustLevel


def test_non_elevation_blocks_unauthorized_integrity_elevation() -> None:
    """Verify that unverified elevation from UNTRUSTED to higher integrity raises TrustElevationError."""
    # UNTRUSTED -> USER_CONTROLLED
    with pytest.raises(TrustElevationError) as exc1:
        ElevationPolicy.assert_non_elevation(
            source_integrity=IntegrityLabel.UNTRUSTED,
            proposed_integrity=IntegrityLabel.USER_CONTROLLED,
            is_cryptographic_verification=False,
            is_independent_verifier=False,
        )
    assert "Non-Elevation Axiom Violated" in str(exc1.value)

    # UNTRUSTED -> SYSTEM_TRUSTED
    with pytest.raises(TrustElevationError) as exc2:
        ElevationPolicy.assert_non_elevation(
            source_integrity=IntegrityLabel.UNTRUSTED,
            proposed_integrity=IntegrityLabel.SYSTEM_TRUSTED,
            is_cryptographic_verification=False,
            is_independent_verifier=False,
        )
    assert "Non-Elevation Axiom Violated" in str(exc2.value)


def test_non_elevation_allows_cryptographic_and_independent_verification() -> None:
    """Verify that verified data can legally elevate integrity."""
    # Cryptographic match allows elevation
    ElevationPolicy.assert_non_elevation(
        source_integrity=IntegrityLabel.UNTRUSTED,
        proposed_integrity=IntegrityLabel.SYSTEM_TRUSTED,
        is_cryptographic_verification=True,
    )

    # Independent verifier allows elevation
    ElevationPolicy.assert_non_elevation(
        source_integrity=IntegrityLabel.UNTRUSTED,
        proposed_integrity=IntegrityLabel.USER_CONTROLLED,
        is_independent_verifier=True,
    )


def test_trust_level_elevation_blocks_unverified_claims() -> None:
    """Verify that model claims cannot elevate TrustLevel."""
    with pytest.raises(TrustElevationError):
        ElevationPolicy.assert_trust_level_elevation(
            source_trust=TrustLevel.EXTERNAL_UNTRUSTED,
            proposed_trust=TrustLevel.SYSTEM_POLICY,
            is_verified=False,
        )


def test_non_declassification_blocks_arbitrary_downgrade() -> None:
    """Verify that SECRET data cannot be arbitrarily declassified to PUBLIC."""
    with pytest.raises(TrustElevationError):
        ElevationPolicy.assert_non_declassification(
            source_confidentiality=ConfidentialityLabel.SECRET,
            proposed_confidentiality=ConfidentialityLabel.PUBLIC,
            is_authorized_sanitizer=False,
        )

    # Authorized sanitizer passes
    ElevationPolicy.assert_non_declassification(
        source_confidentiality=ConfidentialityLabel.SECRET,
        proposed_confidentiality=ConfidentialityLabel.PUBLIC,
        is_authorized_sanitizer=True,
    )
