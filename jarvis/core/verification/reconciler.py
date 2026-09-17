"""Multi-source bibliographic metadata reconciliation engine."""

import re

from jarvis.core.verification.records import CitationAuthor, CitationRecord, ValidationState


def normalize_title_for_comparison(title: str) -> str:
    """Normalize title string for robust cross-provider fuzzy comparison."""
    clean = re.sub(r"<[^>]+>", "", title).lower()
    clean = re.sub(r"[^\w\s]", " ", clean)
    tokens = [
        w
        for w in clean.split()
        if w not in {"the", "a", "an", "and", "or", "of", "for", "in", "on", "to"}
    ]
    return " ".join(tokens)


def extract_family_names(authors: list[CitationAuthor]) -> set[str]:
    """Extract set of normalized lowercase author family names."""
    names: set[str] = set()
    for a in authors:
        fam = a.family.strip().lower()
        if fam:
            # Handle hyphenated or multi-word family names
            for part in re.split(r"[\s\-]+", fam):
                if len(part) > 1:
                    names.add(part)
        elif a.full_name:
            parts = a.full_name.strip().lower().split()
            if parts:
                names.add(parts[-1])
    return names


def reconcile_provider_records(
    records: list[CitationRecord],
) -> tuple[CitationRecord | None, ValidationState, list[str]]:
    """Reconcile bibliographic metadata records across multiple authoritative providers.

    Returns:
        (reconciled_record, validation_state, issues_list)
    """
    if not records:
        return None, ValidationState.UNVERIFIED, ["No authoritative provider records to reconcile."]

    if len(records) == 1:
        rec = records[0]
        # Single source is partially verified unless it has complete fields
        if rec.title and rec.doi and rec.authors and rec.publication_year:
            return rec, ValidationState.VERIFIED, []
        return (
            rec,
            ValidationState.PARTIALLY_VERIFIED,
            ["Single provider record retrieved with partial fields."],
        )

    issues: list[str] = []

    # 1. Reconcile Titles
    titles = [r.title for r in records if r.title]
    norm_titles = [normalize_title_for_comparison(t) for t in titles]
    if len(set(norm_titles)) > 1:
        # Check token jaccard similarity
        t0_tokens = set(norm_titles[0].split())
        for idx in range(1, len(norm_titles)):
            ti_tokens = set(norm_titles[idx].split())
            if not t0_tokens or not ti_tokens:
                continue
            jaccard = len(t0_tokens & ti_tokens) / len(t0_tokens | ti_tokens)
            if jaccard < 0.60:
                issues.append(
                    f"Title conflict between providers: '{titles[0]}' ({records[0].provider}) vs '{titles[idx]}' ({records[idx].provider})."
                )

    # 2. Reconcile Publication Years
    years = [r.publication_year for r in records if r.publication_year is not None]
    if len(set(years)) > 1:
        min_y, max_y = min(years), max(years)
        if max_y - min_y > 1:
            issues.append(
                f"Publication year conflict between providers: {years} across providers {[r.provider for r in records]}."
            )
        else:
            # 1-year discrepancy (often online-first vs print date)
            issues.append(
                f"Minor 1-year publication date discrepancy ({min_y} vs {max_y}) likely due to online-first vs print publication."
            )

    # 3. Reconcile Authors
    author_sets = [(r.provider, extract_family_names(r.authors)) for r in records if r.authors]
    if len(author_sets) > 1:
        base_prov, base_authors = author_sets[0]
        for comp_prov, comp_authors in author_sets[1:]:
            if base_authors and comp_authors:
                overlap = base_authors & comp_authors
                # If zero author overlap between authoritative sources -> major conflict
                if not overlap:
                    issues.append(
                        f"Author conflict between {base_prov} ({sorted(base_authors)}) and {comp_prov} ({sorted(comp_authors)})."
                    )

    # Determine state based on issues
    has_conflict = any("conflict" in i.lower() for i in issues)
    if has_conflict:
        state = ValidationState.CONFLICT
    elif any("discrepancy" in i.lower() for i in issues):
        state = ValidationState.NEEDS_REVIEW
    else:
        state = ValidationState.VERIFIED

    # Merge into highest-confidence canonical record
    # Sort by provider priority: crossref > pubmed > doi_negotiation > openalex > publisher_html
    priority = {
        "crossref": 5,
        "pubmed": 4,
        "doi_negotiation": 3,
        "openalex": 2,
        "publisher_html": 1,
    }
    sorted_records = sorted(records, key=lambda r: priority.get(r.provider, 0), reverse=True)
    primary = sorted_records[0]

    merged_identifiers: dict[str, str] = {}
    for r in sorted_records:
        merged_identifiers.update(r.identifiers)

    # Prefer DOI from records that have it
    doi_val = next((r.doi for r in sorted_records if r.doi), primary.doi)
    pmid_val = next((r.pmid for r in sorted_records if r.pmid), primary.pmid)
    canonical_url_val = next(
        (r.canonical_url for r in sorted_records if r.canonical_url), primary.canonical_url
    )
    year_val = next(
        (r.publication_year for r in sorted_records if r.publication_year), primary.publication_year
    )
    venue_val = next((r.venue for r in sorted_records if r.venue), primary.venue)

    reconciled = CitationRecord(
        source_id=f"reconciled:{doi_val or primary.source_id}",
        doi=doi_val,
        pmid=pmid_val,
        canonical_url=canonical_url_val,
        title=primary.title,
        authors=primary.authors if primary.authors else sorted_records[-1].authors,
        publication_date=primary.publication_date,
        publication_year=year_val,
        venue=venue_val,
        identifiers=merged_identifiers,
        provider=f"reconciled({'+'.join(r.provider for r in records)})",
        provenance=f"Multi-source reconciliation across: {', '.join(r.provider for r in records)}",
        metadata_confidence=0.99 if state == ValidationState.VERIFIED else 0.75,
        metadata_digest=primary.metadata_digest,
    )

    return reconciled, state, issues
