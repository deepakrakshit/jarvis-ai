"""JARVIS Data Provenance and Lineage Tracking.

Records the immutable origin, cryptographic digest, trust ranking, and derivation
history for every data artifact circulating in the OS.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from jarvis.core.ifc.labels import (
    ConfidentialityLabel,
    IntegrityLabel,
    join_confidentiality_labels,
    meet_integrity_labels,
)
from jarvis.core.trust.taxonomy import TrustLevel, meet_trust_levels


def compute_sha256(data: str | bytes) -> str:
    """Compute standard SHA-256 hexadecimal hash."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


class Provenance(BaseModel):
    """Immutable provenance record attached to all labeled data objects."""

    source_uri: str
    """Canonical URI of data origin (e.g., 'user://prompt', 'https://example.com')."""

    ingestion_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    """UTC timestamp when the data crossed an ingress boundary into JARVIS."""

    artifact_hash: str | None = None
    """Cryptographic SHA-256 digest of original raw payload."""

    source_trust_level: TrustLevel = TrustLevel.EXTERNAL_UNTRUSTED
    """Explicit architectural trust classification."""

    integrity_label: IntegrityLabel = IntegrityLabel.UNTRUSTED
    """FIDES integrity label."""

    confidentiality_label: ConfidentialityLabel = ConfidentialityLabel.PUBLIC
    """FIDES confidentiality label."""

    derivation_history: list[str] = Field(default_factory=list)
    """Audit log of causal transformations applied to this data."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Optional context-specific metadata (e.g. content-type, headers)."""

    model_config = {"frozen": True}

    @classmethod
    def create(
        cls,
        source_uri: str,
        source_trust_level: TrustLevel,
        integrity_label: IntegrityLabel,
        confidentiality_label: ConfidentialityLabel,
        raw_data: str | bytes | None = None,
        derivation: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Provenance":
        """Factory method to construct an initial ingress provenance record."""
        artifact_hash = compute_sha256(raw_data) if raw_data is not None else None
        history = [derivation] if derivation else [f"ingest:{source_uri}"]
        return cls(
            source_uri=source_uri,
            artifact_hash=artifact_hash,
            source_trust_level=source_trust_level,
            integrity_label=integrity_label,
            confidentiality_label=confidentiality_label,
            derivation_history=history,
            metadata=metadata or {},
        )

    @classmethod
    def derive(
        cls,
        parents: Sequence["Provenance"],
        transform_description: str,
        new_source_uri: str | None = None,
        raw_data: str | bytes | None = None,
        override_integrity: IntegrityLabel | None = None,
        override_trust: TrustLevel | None = None,
    ) -> "Provenance":
        """Derive a new provenance from one or more parent records.

        Calculates join/meet lattice values by default:
        - Integrity: Lowest integrity of parents (taint propagation).
        - Confidentiality: Highest confidentiality of parents (sensitivity propagation).
        - TrustLevel: Lowest trust of parents.
        """
        if not parents:
            raise ValueError("Cannot derive provenance from an empty parent sequence.")

        effective_uri = new_source_uri or parents[0].source_uri
        effective_trust = (
            override_trust
            if override_trust is not None
            else meet_trust_levels(*(p.source_trust_level for p in parents))
        )
        effective_integrity = (
            override_integrity
            if override_integrity is not None
            else meet_integrity_labels(*(p.integrity_label for p in parents))
        )
        effective_confidentiality = join_confidentiality_labels(
            *(p.confidentiality_label for p in parents)
        )

        combined_history: list[str] = []
        for p in parents:
            for step in p.derivation_history:
                if step not in combined_history:
                    combined_history.append(step)
        combined_history.append(transform_description)

        artifact_hash = compute_sha256(raw_data) if raw_data is not None else None

        return cls(
            source_uri=effective_uri,
            artifact_hash=artifact_hash,
            source_trust_level=effective_trust,
            integrity_label=effective_integrity,
            confidentiality_label=effective_confidentiality,
            derivation_history=combined_history,
        )
