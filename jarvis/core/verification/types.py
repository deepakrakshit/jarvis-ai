"""JARVIS Verification Domain Types and Contracts.

Defines schemas for external state verifiers, witness bindings, and verification outcomes.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class VerifierType(StrEnum):
    """Categorical classification of verification strategies."""

    SEMANTIC = "SEMANTIC"
    """Validates structural schema, semantic consistency, and non-vacuous outputs."""

    EVIDENCE = "EVIDENCE"
    """Validates empirical evidentiary backing, bibliographic metadata, and claim citations."""

    EXECUTION = "EXECUTION"
    """Validates runtime process exits, container health, sentinels, and sandboxed execution."""

    POLICY = "POLICY"
    """Validates adherence to dynamic security policies, IFC labels, and autonomy bounds."""

    STATE = "STATE"
    """Validates external point-in-time state (filesystem changes, database rows, network API reads)."""


class VerificationVerdict(StrEnum):
    """Definitive outcome verdict of an external verification check."""

    VERIFIED = "VERIFIED"
    """External state independently observed and conforms to expected postconditions."""

    REFUTED = "REFUTED"
    """External state was queried and explicitly contradicts expected postconditions."""

    AMBIGUOUS = "AMBIGUOUS"
    """External state could not be deterministically determined (network timeout, rate limit)."""

    UNVERIFIABLE = "UNVERIFIABLE"
    """Target capability has no independent verification harness or witness endpoint."""


class VerificationWitness(BaseModel):
    """Cryptographic or empirical witness bound to an observed external state."""

    witness_type: str
    witness_uri: str
    observed_hash: str
    observed_version_etag: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationRequest(BaseModel):
    """Specification of an intended verification check submitted to a verifier."""

    task_id: UUID | str
    logical_effect_id: str
    capability_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    target_resource: str | None = None
    expected_precondition: dict[str, Any] | None = None
    expected_postcondition: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    """Comprehensive evaluation outcome of a verifier."""

    verdict: VerificationVerdict
    verified: bool
    verification_method: str
    observed_state_hash: str
    witness: VerificationWitness | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    discrepancies: list[str] = Field(default_factory=list)
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
