"""JARVIS Authoritative Citation Integrity and Epistemic Calibration Subsystem.

Enforces data-driven, zero-fabrication verification of bibliographic citations against
external scholarly metadata providers (Crossref, DOI Content Negotiation, OpenAlex, PubMed,
Publisher HTML). Prohibits hardcoded publication registries in production code.
"""

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from jarvis.core.verification.evidence import (
    ClaimSupportResult,
    ClaimSupportStatus,
    audit_claim_epistemic_calibration,
    audit_claim_evidence_support,
    extract_quantitative_assertions,
)
from jarvis.core.verification.providers.base import (
    BaseCitationProvider,
    compute_metadata_digest,
    normalize_doi,
)
from jarvis.core.verification.records import (
    CitationAuthor,
    CitationRecord,
    CitationValidationResult,
    ValidationState,
)
from jarvis.core.verification.validator import CitationValidator


class CitationMetadata(BaseModel):
    """Structured representation of a bibliographic citation for input validation."""

    title: str = Field(description="Exact title of the cited paper, book, or report.")
    authors: str = Field(default="", description="Authors or originating organization.")
    year: int | None = Field(default=None, description="Actual publication year.")
    venue: str | None = Field(
        default=None, description="Journal, conference, or publishing institution."
    )
    url: str | None = Field(default=None, description="Direct URL link.")
    doi: str | None = Field(default=None, description="Digital Object Identifier.")
    raw_reference: str = Field(default="", description="Full raw citation text.")


# Global default validator instance
_default_validator = CitationValidator()


async def verify_citation_async(
    citation: str | dict[str, Any] | CitationMetadata,
    authoritative_record: CitationRecord | None = None,
    resolve_remote: bool = True,
    validator: CitationValidator | None = None,
) -> CitationValidationResult:
    """Asynchronously verify a citation against authoritative scholarly providers."""
    val = validator or _default_validator
    cit_dict = citation.model_dump() if isinstance(citation, BaseModel) else citation
    return await val.verify_citation(
        citation=cit_dict,
        authoritative_record=authoritative_record,
        resolve_remote=resolve_remote,
    )


def validate_citation_metadata(
    citation: CitationMetadata | dict[str, Any] | str,
    retrieved_sources: list[dict[str, Any]] | None = None,
    authoritative_record: CitationRecord | None = None,
) -> CitationValidationResult:
    """Synchronous interface for citation validation.

    If authoritative_record is supplied, validates immediately against it.
    Otherwise, if an active event loop exists or one can be run, queries providers.
    """
    cit_dict: dict[str, Any] = (
        citation.model_dump()
        if isinstance(citation, BaseModel)
        else ({"raw_reference": citation} if isinstance(citation, str) else citation)
    )

    # If authoritative record is already supplied
    if authoritative_record:
        val = CitationValidator(providers=[])
        # Run synchronous comparison
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # In running loop, run in background thread or task
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(
                        asyncio.run,
                        val.verify_citation(
                            cit_dict,
                            authoritative_record=authoritative_record,
                            resolve_remote=False,
                        ),
                    ).result()
            return loop.run_until_complete(
                val.verify_citation(
                    cit_dict, authoritative_record=authoritative_record, resolve_remote=False
                )
            )
        except RuntimeError:
            return asyncio.run(
                val.verify_citation(
                    cit_dict, authoritative_record=authoritative_record, resolve_remote=False
                )
            )

    # If retrieved search sources were passed, attempt local match first
    if retrieved_sources:
        claimed_title = cit_dict.get("title", "")
        claimed_year = cit_dict.get("year")
        for src in retrieved_sources:
            src_title = src.get("title", "")
            src_year = src.get("detected_year")
            if (
                claimed_title
                and src_title
                and (
                    claimed_title.lower() in src_title.lower()
                    or src_title.lower() in claimed_title.lower()
                )
                and src_year
                and claimed_year
                and claimed_year != src_year
            ):
                issues = [
                    f"Publication year upgraded: cited as {claimed_year}, source indicates {src_year}."
                    if claimed_year > src_year
                    else f"Publication year mismatch: cited as {claimed_year}, source indicates {src_year}."
                ]
                return CitationValidationResult(
                    state=ValidationState.INVALID,
                    claimed_title=claimed_title,
                    claimed_year=claimed_year,
                    issues=issues,
                )

    # Resolve remotely via providers
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run, _default_validator.verify_citation(cit_dict, resolve_remote=True)
                ).result()
        return loop.run_until_complete(
            _default_validator.verify_citation(cit_dict, resolve_remote=True)
        )
    except RuntimeError:
        return asyncio.run(_default_validator.verify_citation(cit_dict, resolve_remote=True))


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
