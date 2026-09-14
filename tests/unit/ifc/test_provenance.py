import pytest
from pydantic import ValidationError

from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.ifc.provenance import Provenance, compute_sha256
from jarvis.core.trust.taxonomy import TrustLevel


def test_provenance_create_and_hashing() -> None:
    """Verify SHA-256 computation and creation of immutable provenance."""
    text = "Hello JARVIS"
    expected_hash = compute_sha256(text)

    prov = Provenance.create(
        source_uri="user://chat",
        source_trust_level=TrustLevel.USER_INPUT,
        integrity_label=IntegrityLabel.USER_CONTROLLED,
        confidentiality_label=ConfidentialityLabel.INTERNAL,
        raw_data=text,
    )

    assert prov.source_uri == "user://chat"
    assert prov.artifact_hash == expected_hash
    assert prov.source_trust_level == TrustLevel.USER_INPUT
    assert prov.integrity_label == IntegrityLabel.USER_CONTROLLED
    assert prov.confidentiality_label == ConfidentialityLabel.INTERNAL
    assert "ingest:user://chat" in prov.derivation_history

    # Invariant: Provenance is frozen/immutable
    with pytest.raises(ValidationError):
        prov.source_uri = "tampered"


def test_provenance_derive_preserves_lineage_and_taint() -> None:
    """Verify derivation history and lattice join/meet across parent provenances."""
    p1 = Provenance.create(
        source_uri="https://api.github.com/repos",
        source_trust_level=TrustLevel.EXTERNAL_UNTRUSTED,
        integrity_label=IntegrityLabel.UNTRUSTED,
        confidentiality_label=ConfidentialityLabel.PUBLIC,
        raw_data="github data",
    )
    p2 = Provenance.create(
        source_uri="vault://credentials/token",
        source_trust_level=TrustLevel.SYSTEM_POLICY,
        integrity_label=IntegrityLabel.SYSTEM_TRUSTED,
        confidentiality_label=ConfidentialityLabel.SECRET,
        raw_data="secret_token",
    )

    derived = Provenance.derive(
        parents=[p1, p2],
        transform_description="merge_api_payload_with_token",
        new_source_uri="agent://task/merged",
        raw_data="merged data",
    )

    assert derived.source_uri == "agent://task/merged"
    # Taint dominates: UNTRUSTED meets SYSTEM_TRUSTED -> UNTRUSTED
    assert derived.integrity_label == IntegrityLabel.UNTRUSTED
    # Sensitivity escalates: PUBLIC joins SECRET -> SECRET
    assert derived.confidentiality_label == ConfidentialityLabel.SECRET
    # Trust meets: EXTERNAL_UNTRUSTED meets SYSTEM_POLICY -> EXTERNAL_UNTRUSTED
    assert derived.source_trust_level == TrustLevel.EXTERNAL_UNTRUSTED
    # Lineage contains parent histories and new transform
    assert "merge_api_payload_with_token" in derived.derivation_history
    assert len(derived.derivation_history) == 3
