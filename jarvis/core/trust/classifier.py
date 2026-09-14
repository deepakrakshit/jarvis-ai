from __future__ import annotations

from typing import TYPE_CHECKING

from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.trust.taxonomy import TrustLevel

if TYPE_CHECKING:
    from jarvis.core.ifc.taint import LabeledData


class InputClassifier:
    """Classifies inputs according to source URI scheme and context."""

    @classmethod
    def classify_source(
        cls,
        source_uri: str,
    ) -> tuple[TrustLevel, IntegrityLabel, ConfidentialityLabel]:
        """Determine initial trust, integrity, and confidentiality labels from a URI."""
        uri_lower = source_uri.lower()

        # 1. System policies & invariants
        if uri_lower.startswith("system://") or uri_lower.startswith("policy://"):
            return (
                TrustLevel.SYSTEM_POLICY,
                IntegrityLabel.SYSTEM_TRUSTED,
                ConfidentialityLabel.INTERNAL,
            )

        # 2. Vault / Secret credentials
        if uri_lower.startswith("vault://") or uri_lower.startswith("secret://"):
            return (
                TrustLevel.SYSTEM_POLICY,
                IntegrityLabel.SYSTEM_TRUSTED,
                ConfidentialityLabel.SECRET,
            )

        # 3. Direct Authenticated User Inputs
        if uri_lower.startswith("user://"):
            return (
                TrustLevel.USER_INPUT,
                IntegrityLabel.USER_CONTROLLED,
                ConfidentialityLabel.INTERNAL,
            )

        # 4. Verified Artifacts
        if uri_lower.startswith("verified-crypto://"):
            return (
                TrustLevel.ARTIFACT_INTEGRITY_VERIFIED,
                IntegrityLabel.SYSTEM_TRUSTED,
                ConfidentialityLabel.INTERNAL,
            )

        if uri_lower.startswith("verified-semantic://"):
            return (
                TrustLevel.ARTIFACT_SEMANTICALLY_VERIFIED,
                IntegrityLabel.USER_CONTROLLED,
                ConfidentialityLabel.INTERNAL,
            )

        # 5. External Email (Private but Untrusted)
        if uri_lower.startswith("email://") or uri_lower.startswith("mail://"):
            return (
                TrustLevel.EXTERNAL_UNTRUSTED,
                IntegrityLabel.UNTRUSTED,
                ConfidentialityLabel.CONFIDENTIAL,
            )

        # 6. Web Fetches (Public and Untrusted)
        if uri_lower.startswith("http://") or uri_lower.startswith("https://"):
            return (
                TrustLevel.EXTERNAL_UNTRUSTED,
                IntegrityLabel.UNTRUSTED,
                ConfidentialityLabel.PUBLIC,
            )

        # 7. Model Generations (Untrusted Proposed Intent)
        if uri_lower.startswith("model://") or uri_lower.startswith("agent://"):
            return (
                TrustLevel.MODEL_GENERATED,
                IntegrityLabel.UNTRUSTED,
                ConfidentialityLabel.INTERNAL,
            )

        # 8. MCP / Third-party Tool Outputs
        if uri_lower.startswith("mcp://") or uri_lower.startswith("tool://"):
            return (
                TrustLevel.EXTERNAL_UNTRUSTED,
                IntegrityLabel.UNTRUSTED,
                ConfidentialityLabel.INTERNAL,
            )

        # Default fallback: Fail safe to External Untrusted & Confidential
        return (
            TrustLevel.EXTERNAL_UNTRUSTED,
            IntegrityLabel.UNTRUSTED,
            ConfidentialityLabel.CONFIDENTIAL,
        )

    @classmethod
    def ingest_text(
        cls,
        text: str,
        source_uri: str,
        override_confidentiality: ConfidentialityLabel | None = None,
    ) -> LabeledData[str]:
        """Ingest raw text and return a fully labeled LabeledData[str] container."""
        from jarvis.core.ifc.taint import create_labeled_string

        trust, integrity, confidentiality = cls.classify_source(source_uri)
        if override_confidentiality is not None:
            confidentiality = override_confidentiality

        return create_labeled_string(
            text=text,
            source_uri=source_uri,
            trust_level=trust,
            integrity=integrity,
            confidentiality=confidentiality,
        )
