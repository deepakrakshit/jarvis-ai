"""Authoritative citation verification engine.

Validates bibliographic citations dynamically against external scholarly providers (Crossref,
DOI Content Negotiation, OpenAlex, PubMed, Publisher HTML) without hardcoded publication data.
"""

import asyncio
import re
from typing import Any

from jarvis.core.logging import get_logger
from jarvis.core.verification.providers.base import BaseCitationProvider, normalize_doi
from jarvis.core.verification.providers.crossref import CrossrefProvider
from jarvis.core.verification.providers.doi_negotiation import DoiNegotiationProvider
from jarvis.core.verification.providers.openalex import OpenAlexProvider
from jarvis.core.verification.providers.publisher_html import PublisherHtmlProvider
from jarvis.core.verification.providers.pubmed import PubMedProvider
from jarvis.core.verification.reconciler import (
    extract_family_names,
    normalize_title_for_comparison,
    reconcile_provider_records,
)
from jarvis.core.verification.records import (
    CitationRecord,
    CitationValidationResult,
    ValidationState,
)

logger = get_logger(__name__)


def extract_claimed_citation_fields(citation_text: str) -> dict[str, Any]:
    """Parse raw citation text into candidate claimed fields."""
    clean = " ".join(citation_text.strip().split())

    # Extract 4-digit year (1900-2099)
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", clean)
    claimed_year = int(year_match.group(1)) if year_match else None

    # Extract DOI
    clean_doi = normalize_doi(clean)

    # Extract URL
    url_match = re.search(r"https?://[^\s)]+", clean)
    claimed_url = url_match.group(0).rstrip(".,;") if url_match else None

    # Extract PMID
    pmid_match = re.search(r"(?:PMID:?\s*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d+)", clean, re.IGNORECASE)
    claimed_pmid = pmid_match.group(1) if pmid_match else None

    # Extract Author & Title heuristics
    # Pattern: 1. Author, A. et al. (Year). Title. Venue. URL
    # or: Author (Year) "Title"
    author_candidate = ""
    title_candidate = clean

    # Strip leading numbering like "1. ", "[1] ", "* "
    text_no_num = re.sub(r"^(?:\d+[\.\)]|\[\d+\]|\*|\-)\s*", "", clean)

    paren_year_match = re.search(r"^(.*?)\s*\((\d{4})\)\.?\s*(.*)", text_no_num)
    if paren_year_match:
        author_candidate = paren_year_match.group(1).strip()
        title_candidate = paren_year_match.group(3).strip()
    else:
        # Check for dot separator before title
        parts = text_no_num.split(". ")
        if len(parts) >= 2:
            author_candidate = parts[0].strip()
            title_candidate = ". ".join(parts[1:]).strip()

    # Clean title candidate (strip URLs, venues if possible)
    if claimed_url:
        title_candidate = title_candidate.replace(claimed_url, "").strip().rstrip(". ")

    return {
        "raw_text": clean,
        "claimed_year": claimed_year,
        "claimed_doi": clean_doi,
        "claimed_url": claimed_url,
        "claimed_pmid": claimed_pmid,
        "claimed_authors": author_candidate,
        "claimed_title": title_candidate,
    }


def parse_claimed_author_family_names(claimed_author_str: str) -> set[str]:
    """Extract family names from a claimed author string (e.g. 'Rajkomar, A. et al.')."""
    clean = claimed_author_str.lower()
    clean = re.sub(r"\b(et\s+al\.?|and|&)\b", " ", clean)
    clean = re.sub(r"[^\w\s]", " ", clean)
    tokens = [w for w in clean.split() if len(w) > 2]
    return set(tokens)


class CitationValidator:
    """Dynamic citation validator backed by external authoritative scholarly providers."""

    def __init__(
        self,
        providers: list[BaseCitationProvider] | None = None,
    ) -> None:
        self.providers = providers or [
            CrossrefProvider(),
            DoiNegotiationProvider(),
            OpenAlexProvider(),
            PubMedProvider(),
            PublisherHtmlProvider(),
        ]

    async def resolve_authoritative_metadata(
        self,
        doi: str | None = None,
        pmid: str | None = None,
        url: str | None = None,
        title_query: str | None = None,
    ) -> tuple[CitationRecord | None, ValidationState, list[str], dict[str, CitationRecord]]:
        """Query external providers concurrently and reconcile metadata records."""
        tasks: list[Any] = []

        # 1. DOI-First resolution across providers
        clean_doi = normalize_doi(doi or url)
        if clean_doi:
            for p in self.providers:
                tasks.append(p.resolve_by_doi(clean_doi))

        # 2. PMID resolution if available
        if pmid:
            for p in self.providers:
                tasks.append(p.resolve_by_pmid(pmid))

        # 3. Direct URL resolution (Publisher HTML)
        if url and not clean_doi:
            for p in self.providers:
                tasks.append(p.resolve_by_url(url))

        # 4. Fallback search by title query if no strong identifier exists
        if not clean_doi and not pmid and not url and title_query:
            for p in self.providers:
                tasks.append(p.search(title_query, limit=2))

        if not tasks:
            return (
                None,
                ValidationState.UNVERIFIED,
                ["No identifiers or query provided for resolution."],
                {},
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidate_records: list[CitationRecord] = []
        for res in results:
            if isinstance(res, CitationRecord):
                candidate_records.append(res)
            elif isinstance(res, list):
                for item in res:
                    if isinstance(item, CitationRecord):
                        candidate_records.append(item)

        if not candidate_records:
            return (
                None,
                ValidationState.UNVERIFIED,
                ["No authoritative metadata found across configured providers."],
                {},
            )

        provider_map: dict[str, CitationRecord] = {}
        reconcile_records: list[CitationRecord] = []

        if title_query and not clean_doi:
            norm_q = normalize_title_for_comparison(title_query)
            q_tokens = set(norm_q.split())
            best_by_provider: dict[str, tuple[float, CitationRecord]] = {}

            for rec in candidate_records:
                if not rec.title:
                    continue
                norm_t = normalize_title_for_comparison(rec.title)
                t_tokens = set(norm_t.split())
                if not q_tokens or not t_tokens:
                    continue
                jaccard = len(q_tokens & t_tokens) / len(q_tokens | t_tokens)
                if norm_q in norm_t or norm_t in norm_q:
                    jaccard = max(jaccard, 0.75)

                if jaccard >= 0.35:
                    prev_score = best_by_provider.get(rec.provider, (-1.0, rec))[0]
                    if jaccard > prev_score:
                        best_by_provider[rec.provider] = (jaccard, rec)

            for prov, (_score, rec) in best_by_provider.items():
                reconcile_records.append(rec)
                provider_map[prov] = rec

            if not reconcile_records:
                # If no records exceeded threshold, retain top candidate to diagnose mismatch
                reconcile_records = candidate_records[:1]
                provider_map[reconcile_records[0].provider] = reconcile_records[0]
        else:
            reconcile_records = candidate_records
            for rec in candidate_records:
                provider_map[rec.provider] = rec

        reconciled, state, issues = reconcile_provider_records(reconcile_records)
        return reconciled, state, issues, provider_map

    async def verify_citation(
        self,
        citation: str | dict[str, Any],
        authoritative_record: CitationRecord | None = None,
        resolve_remote: bool = True,
    ) -> CitationValidationResult:
        """Verify a single bibliographic citation against authoritative providers."""
        if isinstance(citation, str):
            claimed = extract_claimed_citation_fields(citation)
        else:
            claimed = {
                "raw_text": citation.get("raw_reference", ""),
                "claimed_year": citation.get("year"),
                "claimed_doi": normalize_doi(citation.get("doi") or citation.get("url")),
                "claimed_url": citation.get("url"),
                "claimed_pmid": citation.get("pmid"),
                "claimed_authors": citation.get("authors", ""),
                "claimed_title": citation.get("title", ""),
            }

        claimed_doi = claimed.get("claimed_doi")
        claimed_url = claimed.get("claimed_url")
        claimed_pmid = claimed.get("claimed_pmid")
        claimed_year = claimed.get("claimed_year")
        claimed_authors = claimed.get("claimed_authors", "")
        claimed_title = claimed.get("claimed_title", "")

        issues: list[str] = []
        provider_map: dict[str, CitationRecord] = {}
        reconciled = authoritative_record
        prov_state = (
            ValidationState.VERIFIED if authoritative_record else ValidationState.UNVERIFIED
        )

        if not reconciled and resolve_remote:
            (
                reconciled,
                prov_state,
                prov_issues,
                provider_map,
            ) = await self.resolve_authoritative_metadata(
                doi=claimed_doi,
                pmid=claimed_pmid,
                url=claimed_url,
                title_query=claimed_title if len(claimed_title) > 10 else None,
            )
            issues.extend(prov_issues)

        if not reconciled:
            return CitationValidationResult(
                state=ValidationState.UNVERIFIED,
                reconciled_record=None,
                claimed_title=claimed_title,
                claimed_authors=claimed_authors,
                claimed_year=claimed_year,
                issues=issues
                or ["Could not resolve authoritative bibliographic metadata for citation."],
                matched_providers=[],
                provider_records={},
            )

        # Propagate provider conflict
        if prov_state == ValidationState.CONFLICT:
            return CitationValidationResult(
                state=ValidationState.CONFLICT,
                reconciled_record=reconciled,
                claimed_title=claimed_title,
                claimed_authors=claimed_authors,
                claimed_year=claimed_year,
                issues=issues,
                matched_providers=list(provider_map.keys()),
                provider_records=provider_map,
            )

        # Author verification
        if claimed_authors:
            claimed_family_names = parse_claimed_author_family_names(claimed_authors)
            authoritative_family_names = extract_family_names(reconciled.authors)

            if authoritative_family_names and claimed_family_names:
                overlap = claimed_family_names & authoritative_family_names
                if not overlap:
                    auth_sample = [f"{a.given} {a.family}".strip() for a in reconciled.authors[:5]]
                    issues.append(
                        f"Claimed author '{claimed_authors}' is not present in authoritative author metadata: {auth_sample}."
                    )
                    return CitationValidationResult(
                        state=ValidationState.INVALID,
                        reconciled_record=reconciled,
                        claimed_title=claimed_title,
                        claimed_authors=claimed_authors,
                        claimed_year=claimed_year,
                        issues=issues,
                        matched_providers=list(provider_map.keys()),
                        provider_records=provider_map,
                    )

        # Publication Year verification
        if claimed_year is not None and reconciled.publication_year is not None:
            if claimed_year > reconciled.publication_year:
                issues.append(
                    f"Publication year upgraded: cited as {claimed_year}, but authoritative publication year is {reconciled.publication_year}."
                )
                return CitationValidationResult(
                    state=ValidationState.INVALID,
                    reconciled_record=reconciled,
                    claimed_title=claimed_title,
                    claimed_authors=claimed_authors,
                    claimed_year=claimed_year,
                    issues=issues,
                    matched_providers=list(provider_map.keys()),
                    provider_records=provider_map,
                )
            elif claimed_year < reconciled.publication_year:
                issues.append(
                    f"Publication year mismatch: cited as {claimed_year}, but authoritative publication year is {reconciled.publication_year}."
                )
                return CitationValidationResult(
                    state=ValidationState.INVALID,
                    reconciled_record=reconciled,
                    claimed_title=claimed_title,
                    claimed_authors=claimed_authors,
                    claimed_year=claimed_year,
                    issues=issues,
                    matched_providers=list(provider_map.keys()),
                    provider_records=provider_map,
                )

        # Title verification
        if claimed_title and reconciled.title:
            norm_c = normalize_title_for_comparison(claimed_title)
            norm_a = normalize_title_for_comparison(reconciled.title)
            tokens_c = set(norm_c.split())
            tokens_a = set(norm_a.split())
            if tokens_c and tokens_a:
                overlap_ratio = len(tokens_c & tokens_a) / max(len(tokens_c), len(tokens_a))
                if overlap_ratio < 0.40 and norm_c not in norm_a and norm_a not in norm_c:
                    issues.append(
                        f"Title mismatch: claimed '{claimed_title}' does not match authoritative title '{reconciled.title}'."
                    )
                    return CitationValidationResult(
                        state=ValidationState.INVALID,
                        reconciled_record=reconciled,
                        claimed_title=claimed_title,
                        claimed_authors=claimed_authors,
                        claimed_year=claimed_year,
                        issues=issues,
                        matched_providers=list(provider_map.keys()),
                        provider_records=provider_map,
                    )

        # Determine final state
        # If all fields present and verified: VERIFIED
        # If missing author or year in authoritative record: PARTIALLY_VERIFIED
        if not reconciled.authors or reconciled.publication_year is None:
            final_state = ValidationState.PARTIALLY_VERIFIED
        elif any("discrepancy" in i.lower() for i in issues):
            final_state = ValidationState.NEEDS_REVIEW
        else:
            final_state = ValidationState.VERIFIED

        return CitationValidationResult(
            state=final_state,
            reconciled_record=reconciled,
            claimed_title=claimed_title,
            claimed_authors=claimed_authors,
            claimed_year=claimed_year,
            issues=issues,
            matched_providers=list(provider_map.keys()),
            provider_records=provider_map,
        )
