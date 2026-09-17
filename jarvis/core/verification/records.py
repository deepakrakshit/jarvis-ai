"""Canonical Citation Record and Validation State Models.

Defines provider-independent schemas for authoritative bibliographic metadata,
multi-source reconciliation results, and explicit validation states.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ValidationState(StrEnum):
    """Explicit verification lifecycle states for citations."""

    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    CONFLICT = "CONFLICT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INVALID = "INVALID"
    UNVERIFIED = "UNVERIFIED"


class CitationAuthor(BaseModel):
    """Structured representation of a publication author or creator."""

    family: str = Field(default="", description="Family / last name.")
    given: str = Field(default="", description="Given / first / middle names.")
    full_name: str = Field(default="", description="Complete or display author name.")


class CitationRecord(BaseModel):
    """Provider-independent canonical bibliographic record."""

    source_id: str = Field(description="Unique source or work identifier (e.g. DOI or URL).")
    doi: str | None = Field(
        default=None, description="Normalized lowercase Digital Object Identifier."
    )
    pmid: str | None = Field(default=None, description="PubMed ID if available.")
    canonical_url: str | None = Field(
        default=None, description="Authoritative canonical publisher or repository URL."
    )
    title: str = Field(description="Canonical title of the publication.")
    authors: list[CitationAuthor] = Field(
        default_factory=list, description="Structured author list."
    )
    publication_date: str | None = Field(
        default=None, description="Full or partial publication date string (YYYY-MM-DD or YYYY-MM)."
    )
    publication_year: int | None = Field(
        default=None, description="Authoritative 4-digit publication year."
    )
    venue: str | None = Field(default=None, description="Journal, conference, or publisher name.")
    identifiers: dict[str, str] = Field(
        default_factory=dict, description="Dictionary of known external identifiers."
    )
    provider: str = Field(
        description="Name of metadata provider (e.g. 'crossref', 'openalex', 'pubmed', 'doi_negotiation', 'publisher_html')."
    )
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of metadata retrieval.",
    )
    provenance: str = Field(default="", description="Description of the retrieval path and query.")
    metadata_confidence: float = Field(
        default=1.0, description="Confidence score of the retrieved record (0.0 to 1.0)."
    )
    metadata_digest: str = Field(
        default="", description="Cryptographic or content hash of raw provider metadata."
    )


class CitationValidationResult(BaseModel):
    """Structured validation outcome for a citation against authoritative sources."""

    state: ValidationState = Field(
        default=ValidationState.UNVERIFIED, description="Explicit verification state."
    )
    reconciled_record: CitationRecord | None = Field(
        default=None, description="Reconciled authoritative record if resolved."
    )
    claimed_title: str = Field(default="", description="Title as claimed in the user citation.")
    claimed_authors: str = Field(
        default="", description="Authors string as claimed in the user citation."
    )
    claimed_year: int | None = Field(
        default=None, description="Publication year as claimed in the user citation."
    )
    issues: list[str] = Field(
        default_factory=list, description="Specific discrepancies or validation issues found."
    )
    matched_providers: list[str] = Field(
        default_factory=list, description="List of providers that successfully resolved the work."
    )
    provider_records: dict[str, Any] = Field(
        default_factory=dict, description="Raw or normalized records by provider name."
    )

    @property
    def is_valid(self) -> bool:
        """Helper compatibility property: true only when verified or partially verified."""
        return self.state in (ValidationState.VERIFIED, ValidationState.PARTIALLY_VERIFIED)

    @property
    def is_year_upgraded(self) -> bool:
        """Helper compatibility property: true if publication year was upgraded."""
        return any("upgraded" in issue.lower() for issue in self.issues)

    @property
    def detected_real_year(self) -> int | None:
        """Helper compatibility property: detected authoritative year."""
        if self.reconciled_record and self.reconciled_record.publication_year:
            return self.reconciled_record.publication_year
        return None
