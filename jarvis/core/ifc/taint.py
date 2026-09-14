"""JARVIS Information-Flow Control Taint Tracking Container.

Wraps raw payloads in an immutable LabeledData[T] container that enforces
automatic propagation of provenance, integrity taint, and confidentiality escalation.
"""

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.ifc.provenance import Provenance
from jarvis.core.trust.taxonomy import TrustLevel

T = TypeVar("T")
U = TypeVar("U")


class LabeledData(BaseModel, Generic[T]):
    """Immutable data wrapper binding a payload to its FIDES labels and provenance."""

    data: T
    """The underlying raw payload (text, dict, bytes, etc.)."""

    provenance: Provenance
    """The cryptographic and lineage provenance of this data."""

    model_config = {"frozen": True, "arbitrary_types_allowed": True}

    @property
    def integrity(self) -> IntegrityLabel:
        """Shortcut to current integrity label."""
        return self.provenance.integrity_label

    @property
    def confidentiality(self) -> ConfidentialityLabel:
        """Shortcut to current confidentiality label."""
        return self.provenance.confidentiality_label

    @property
    def trust_level(self) -> TrustLevel:
        """Shortcut to current trust level."""
        return self.provenance.source_trust_level

    @property
    def is_untrusted(self) -> bool:
        """Return True if this data is tainted as UNTRUSTED."""
        return self.provenance.integrity_label == IntegrityLabel.UNTRUSTED

    @property
    def is_secret(self) -> bool:
        """Return True if this data has SECRET confidentiality."""
        return self.provenance.confidentiality_label == ConfidentialityLabel.SECRET

    def derive(
        self,
        new_payload: U,
        transform_description: str,
        override_integrity: IntegrityLabel | None = None,
        override_trust: TrustLevel | None = None,
    ) -> "LabeledData[U]":
        """Derive a new LabeledData container preserving or elevating taint according to rules."""
        raw_bytes = (
            str(new_payload).encode("utf-8") if isinstance(new_payload, (str, dict, list)) else None
        )
        new_prov = Provenance.derive(
            parents=[self.provenance],
            transform_description=transform_description,
            raw_data=raw_bytes,
            override_integrity=override_integrity,
            override_trust=override_trust,
        )
        return LabeledData[U](data=new_payload, provenance=new_prov)

    @classmethod
    def combine(
        cls,
        items: list["LabeledData[Any]"],
        combiner: Callable[[list[Any]], U],
        transform_description: str,
    ) -> "LabeledData[U]":
        """Combine multiple labeled data items using a transform function.

        Taint propagation invariant:
        - Result integrity = meet(all integrities) [Taint dominates]
        - Result confidentiality = join(all confidentialities) [Highest sensitivity dominates]
        """
        if not items:
            raise ValueError("Cannot combine an empty list of LabeledData items.")

        raw_payloads = [item.data for item in items]
        combined_payload = combiner(raw_payloads)
        parents = [item.provenance for item in items]

        raw_bytes = (
            str(combined_payload).encode("utf-8")
            if isinstance(combined_payload, (str, dict, list))
            else None
        )
        new_prov = Provenance.derive(
            parents=parents,
            transform_description=transform_description,
            raw_data=raw_bytes,
        )
        return LabeledData[U](data=combined_payload, provenance=new_prov)


def create_labeled_string(
    text: str,
    source_uri: str,
    trust_level: TrustLevel = TrustLevel.EXTERNAL_UNTRUSTED,
    integrity: IntegrityLabel = IntegrityLabel.UNTRUSTED,
    confidentiality: ConfidentialityLabel = ConfidentialityLabel.PUBLIC,
    metadata: dict[str, Any] | None = None,
) -> LabeledData[str]:
    """Helper to instantiate a LabeledData[str] with initial ingress provenance."""
    prov = Provenance.create(
        source_uri=source_uri,
        source_trust_level=trust_level,
        integrity_label=integrity,
        confidentiality_label=confidentiality,
        raw_data=text,
        metadata=metadata,
    )
    return LabeledData[str](data=text, provenance=prov)
