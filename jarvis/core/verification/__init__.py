"""JARVIS External-State Verifier, Effect Receipts, and Epistemic Calibration Subsystem."""

from jarvis.core.verification.base import BaseVerifier
from jarvis.core.verification.citation import (
    BaseCitationProvider,
    CitationAuthor,
    CitationMetadata,
    CitationRecord,
    CitationValidationResult,
    CitationValidator,
    ClaimSupportResult,
    ClaimSupportStatus,
    ValidationState,
    audit_claim_epistemic_calibration,
    audit_claim_evidence_support,
    compute_metadata_digest,
    extract_quantitative_assertions,
    normalize_doi,
    validate_citation_metadata,
    verify_citation_async,
)
from jarvis.core.verification.evidence_verifier import EvidenceVerifier
from jarvis.core.verification.execution import ExecutionVerifier
from jarvis.core.verification.policy import PolicyVerifier
from jarvis.core.verification.receipt import EffectReceipt, ReceiptMinter, ReceiptStore
from jarvis.core.verification.registry import VerifierRegistry
from jarvis.core.verification.semantic import SemanticVerifier
from jarvis.core.verification.state import StateVerifier
from jarvis.core.verification.types import (
    VerificationRequest,
    VerificationResult,
    VerificationVerdict,
    VerificationWitness,
    VerifierType,
)

__all__ = [
    "BaseCitationProvider",
    "BaseVerifier",
    "CitationAuthor",
    "CitationMetadata",
    "CitationRecord",
    "CitationValidationResult",
    "CitationValidator",
    "ClaimSupportResult",
    "ClaimSupportStatus",
    "EffectReceipt",
    "EvidenceVerifier",
    "ExecutionVerifier",
    "PolicyVerifier",
    "ReceiptMinter",
    "ReceiptStore",
    "SemanticVerifier",
    "StateVerifier",
    "ValidationState",
    "VerificationRequest",
    "VerificationResult",
    "VerificationVerdict",
    "VerificationWitness",
    "VerifierRegistry",
    "VerifierType",
    "audit_claim_epistemic_calibration",
    "audit_claim_evidence_support",
    "compute_metadata_digest",
    "extract_quantitative_assertions",
    "normalize_doi",
    "validate_citation_metadata",
    "verify_citation_async",
]
