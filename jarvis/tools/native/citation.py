"""JARVIS Native Citation Verification Tool.

Provides safe, authoritative resolution and validation of academic literature citations
against Crossref, DOI Content Negotiation, OpenAlex, PubMed, and Publisher metadata.
"""

from typing import Any

from jarvis.core.verification.citation import (
    ValidationState,
    verify_citation_async,
)


async def verify_citation(
    query: str | None = None,
    doi: str | None = None,
    url: str | None = None,
    claimed_authors: str | None = None,
    claimed_year: int | None = None,
    claimed_title: str | None = None,
) -> dict[str, Any]:
    """Resolve and verify academic publication metadata against authoritative scholarly providers."""
    input_payload: dict[str, Any] = {
        "raw_reference": query or "",
        "doi": doi,
        "url": url,
        "authors": claimed_authors or "",
        "year": claimed_year,
        "title": claimed_title or (query if query and not doi and not url else ""),
    }

    result = await verify_citation_async(input_payload, resolve_remote=True)

    record_dict: dict[str, Any] | None = None
    if result.reconciled_record:
        rec = result.reconciled_record
        record_dict = {
            "title": rec.title,
            "authors": [f"{a.given} {a.family}".strip() or a.full_name for a in rec.authors],
            "publication_year": rec.publication_year,
            "publication_date": rec.publication_date,
            "venue": rec.venue,
            "doi": rec.doi,
            "pmid": rec.pmid,
            "canonical_url": rec.canonical_url,
            "provider": rec.provider,
        }

    return {
        "validation_state": result.state.value,
        "is_verified": result.state
        in (ValidationState.VERIFIED, ValidationState.PARTIALLY_VERIFIED),
        "authoritative_record": record_dict,
        "issues": result.issues,
        "matched_providers": result.matched_providers,
    }
