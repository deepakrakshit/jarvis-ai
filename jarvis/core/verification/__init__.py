"""JARVIS External-State Verifier, Citation Integrity, and Epistemic Calibration Subsystem."""

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

__all__ = [
    "BaseCitationProvider",
    "CitationAuthor",
    "CitationMetadata",
    "CitationRecord",
    "CitationValidationResult",
    "CitationValidator",
    "ClaimSupportResult",
    "ClaimSupportStatus",
    "ValidationState",
    "audit_claim_epistemic_calibration",
    "audit_claim_evidence_support",
    "compute_metadata_digest",
    "extract_quantitative_assertions",
    "normalize_doi",
    "validate_citation_metadata",
    "verify_citation_async",
]
