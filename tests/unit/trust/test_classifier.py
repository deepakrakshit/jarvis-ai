"""Tests for InputClassifier source URI classification and labeling."""

from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.trust.classifier import InputClassifier
from jarvis.core.trust.taxonomy import TrustLevel


def test_classify_system_and_policy() -> None:
    """Verify system policies map to SYSTEM_POLICY and SYSTEM_TRUSTED."""
    trust, integrity, conf = InputClassifier.classify_source("system://core/policy/safe_mode")
    assert trust == TrustLevel.SYSTEM_POLICY
    assert integrity == IntegrityLabel.SYSTEM_TRUSTED
    assert conf == ConfidentialityLabel.INTERNAL


def test_classify_secrets_and_vault() -> None:
    """Verify vault URIs map to SECRET confidentiality."""
    trust, integrity, conf = InputClassifier.classify_source("vault://api_keys/gemini")
    assert trust == TrustLevel.SYSTEM_POLICY
    assert integrity == IntegrityLabel.SYSTEM_TRUSTED
    assert conf == ConfidentialityLabel.SECRET


def test_classify_user_input() -> None:
    """Verify user prompts map to USER_INPUT and USER_CONTROLLED."""
    trust, integrity, conf = InputClassifier.classify_source("user://prompt/cli")
    assert trust == TrustLevel.USER_INPUT
    assert integrity == IntegrityLabel.USER_CONTROLLED
    assert conf == ConfidentialityLabel.INTERNAL


def test_classify_web_and_external() -> None:
    """Verify web crawl URLs map to EXTERNAL_UNTRUSTED and UNTRUSTED."""
    trust, integrity, conf = InputClassifier.classify_source("https://example.com/article")
    assert trust == TrustLevel.EXTERNAL_UNTRUSTED
    assert integrity == IntegrityLabel.UNTRUSTED
    assert conf == ConfidentialityLabel.PUBLIC


def test_classify_email() -> None:
    """Verify emails map to EXTERNAL_UNTRUSTED, UNTRUSTED, and CONFIDENTIAL."""
    trust, integrity, conf = InputClassifier.classify_source("email://inbox/msg123")
    assert trust == TrustLevel.EXTERNAL_UNTRUSTED
    assert integrity == IntegrityLabel.UNTRUSTED
    assert conf == ConfidentialityLabel.CONFIDENTIAL


def test_ingest_text_produces_valid_labeled_data() -> None:
    """Verify helper ingest_text generates LabeledData container with provenance."""
    labeled = InputClassifier.ingest_text("Analyze this webpage", "https://docs.python.org")
    assert labeled.data == "Analyze this webpage"
    assert labeled.provenance.source_uri == "https://docs.python.org"
    assert labeled.integrity == IntegrityLabel.UNTRUSTED
    assert labeled.confidentiality == ConfidentialityLabel.PUBLIC
    assert labeled.trust_level == TrustLevel.EXTERNAL_UNTRUSTED
    assert labeled.provenance.artifact_hash is not None
