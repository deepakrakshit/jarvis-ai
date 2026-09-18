"""JARVIS Governed Temporal Memory Plane Schemas and Data Models.

Defines typed schemas for memory records, epistemic calibration, optimistic
concurrency parameters, and temporal validity intervals.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.core.trust.taxonomy import TrustLevel


class EpistemicStatus(StrEnum):
    """Calibrated epistemic status governing trust in a memory record."""

    PROPOSED = "PROPOSED"
    """Proposed by model or untrusted external source; unverified hypothesis."""

    OBSERVED = "OBSERVED"
    """Empirically observed in interaction, tool observation, or user statement."""

    VERIFIED_INTERNAL = "VERIFIED_INTERNAL"
    """Verified against internal state machines, cryptographic hashes, or system invariants."""

    VERIFIED_EXTERNAL = "VERIFIED_EXTERNAL"
    """Verified against authoritative external ground truth via independent verifiers."""


# Epistemic rank: higher integer = higher certainty / epistemic rigor
_EPISTEMIC_RANK: dict[EpistemicStatus, int] = {
    EpistemicStatus.PROPOSED: 0,
    EpistemicStatus.OBSERVED: 1,
    EpistemicStatus.VERIFIED_INTERNAL: 2,
    EpistemicStatus.VERIFIED_EXTERNAL: 3,
}


def get_epistemic_rank(status: EpistemicStatus) -> int:
    """Return numerical rank of an epistemic status."""
    return _EPISTEMIC_RANK[status]


def is_epistemic_at_least(actual: EpistemicStatus, minimum: EpistemicStatus) -> bool:
    """Return True if actual epistemic status meets or exceeds the minimum required."""
    return _EPISTEMIC_RANK[actual] >= _EPISTEMIC_RANK[minimum]


class MemoryCategory(StrEnum):
    """Categorical domain for governed memories."""

    USER_PREFERENCE = "USER_PREFERENCE"
    """Explicit or inferred user preferences, working habits, and interaction settings."""

    PROJECT_FACT = "PROJECT_FACT"
    """Architectural, technical, or codebase facts bound to specific workspaces."""

    SYSTEM_PROCEDURE = "SYSTEM_PROCEDURE"
    """Operational steps, verified runbooks, and workflow procedures."""

    EPISODIC_NOTE = "EPISODIC_NOTE"
    """Session-specific notes, turn observations, and conversational breadcrumbs."""

    SESSION_SUMMARY = "SESSION_SUMMARY"
    """Compacted summaries of past sessions and background runs."""


class MemoryRecord(BaseModel):
    """Immutable revision of a governed memory record."""

    memory_id: str = Field(default_factory=lambda: f"mem-{uuid4()}")
    tenant_id: str = "default"
    user_id: str = "default_user"
    category: MemoryCategory
    key: str
    content: str
    embedding: list[float] | None = None
    embedding_ref: str | None = None
    epistemic_status: EpistemicStatus = EpistemicStatus.PROPOSED
    source_trust_level: TrustLevel = TrustLevel.EXTERNAL_UNTRUSTED
    source_uri: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_until: datetime | None = None
    superseded_by: str | None = None
    supersedes_id: str | None = None
    version: int = 1
    acl: list[str] = Field(default_factory=lambda: ["default_user"])
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Check if memory is active (not superseded and within validity interval)."""
        now = datetime.now(UTC)
        if self.superseded_by is not None:
            return False
        if self.valid_until is not None and self.valid_until <= now:
            return False
        return self.valid_from <= now

    def is_valid_as_of(self, query_time: datetime) -> bool:
        """Check point-in-time validity for temporal queries."""
        if self.valid_from > query_time:
            return False
        return not (self.valid_until is not None and self.valid_until <= query_time)


class MemoryWriteProposal(BaseModel):
    """Proposed memory creation or revision submitted to the memory plane."""

    tenant_id: str = "default"
    user_id: str = "default_user"
    category: MemoryCategory
    key: str
    content: str
    epistemic_status: EpistemicStatus = EpistemicStatus.PROPOSED
    source_trust_level: TrustLevel = TrustLevel.EXTERNAL_UNTRUSTED
    source_uri: str | None = None
    expected_version: int | None = None  # None for create; int for revision OCC
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryQuery(BaseModel):
    """Structured query for retrieving memories with temporal and scope filtering."""

    tenant_id: str = "default"
    user_id: str = "default_user"
    category: MemoryCategory | None = None
    key: str | None = None
    as_of: datetime | None = None
    min_epistemic_status: EpistemicStatus | None = None
    include_superseded: bool = False
    limit: int = Field(default=50, ge=1, le=500)
