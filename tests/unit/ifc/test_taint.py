import pytest
from pydantic import ValidationError

from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.ifc.taint import LabeledData, create_labeled_string
from jarvis.core.trust.taxonomy import TrustLevel


def test_labeled_data_basic_attributes() -> None:
    """Verify properties and immutability of LabeledData."""
    data = create_labeled_string(
        text="Sample prompt",
        source_uri="user://cli",
        trust_level=TrustLevel.USER_INPUT,
        integrity=IntegrityLabel.USER_CONTROLLED,
        confidentiality=ConfidentialityLabel.INTERNAL,
    )

    assert data.data == "Sample prompt"
    assert data.integrity == IntegrityLabel.USER_CONTROLLED
    assert data.confidentiality == ConfidentialityLabel.INTERNAL
    assert data.trust_level == TrustLevel.USER_INPUT
    assert not data.is_untrusted
    assert not data.is_secret

    # Immutability
    with pytest.raises(ValidationError):
        data.data = "tampered"


def test_labeled_data_derive_propagation() -> None:
    """Verify derive preserves or transforms payload while updating provenance."""
    original = create_labeled_string(
        text="Raw untrusted article text",
        source_uri="https://suspicious.site/article",
        trust_level=TrustLevel.EXTERNAL_UNTRUSTED,
        integrity=IntegrityLabel.UNTRUSTED,
        confidentiality=ConfidentialityLabel.PUBLIC,
    )

    derived = original.derive(
        new_payload="Summary: article text",
        transform_description="summarize_text",
    )

    assert derived.data == "Summary: article text"
    assert derived.integrity == IntegrityLabel.UNTRUSTED
    assert derived.confidentiality == ConfidentialityLabel.PUBLIC
    assert derived.is_untrusted
    assert "summarize_text" in derived.provenance.derivation_history


def test_labeled_data_combine_lattice_join_and_meet() -> None:
    """Verify combining multiple labeled items enforces taint and sensitivity rules."""
    user_query = create_labeled_string(
        text="Find summary of",
        source_uri="user://prompt",
        trust_level=TrustLevel.USER_INPUT,
        integrity=IntegrityLabel.USER_CONTROLLED,
        confidentiality=ConfidentialityLabel.INTERNAL,
    )
    untrusted_web = create_labeled_string(
        text="Malicious web page payload",
        source_uri="https://evil.example.com",
        trust_level=TrustLevel.EXTERNAL_UNTRUSTED,
        integrity=IntegrityLabel.UNTRUSTED,
        confidentiality=ConfidentialityLabel.PUBLIC,
    )
    secret_vault = create_labeled_string(
        text="sk-proj-super-secret-key-1234",
        source_uri="vault://keys/api",
        trust_level=TrustLevel.SYSTEM_POLICY,
        integrity=IntegrityLabel.SYSTEM_TRUSTED,
        confidentiality=ConfidentialityLabel.SECRET,
    )

    combined = LabeledData.combine(
        items=[user_query, untrusted_web, secret_vault],
        combiner=lambda parts: " ".join(parts),
        transform_description="concat_three_sources",
    )

    # Invariant: Lowest integrity dominates -> UNTRUSTED
    assert combined.integrity == IntegrityLabel.UNTRUSTED
    assert combined.is_untrusted

    # Invariant: Highest confidentiality dominates -> SECRET
    assert combined.confidentiality == ConfidentialityLabel.SECRET
    assert combined.is_secret

    # Invariant: Lowest trust level dominates -> EXTERNAL_UNTRUSTED
    assert combined.trust_level == TrustLevel.EXTERNAL_UNTRUSTED
