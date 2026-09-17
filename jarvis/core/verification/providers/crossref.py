"""Crossref REST API authoritative metadata provider adapter."""

from typing import Any

import httpx

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.core.verification.providers.base import (
    BaseCitationProvider,
    compute_metadata_digest,
    normalize_doi,
)
from jarvis.core.verification.records import CitationAuthor, CitationRecord

logger = get_logger(__name__)


class CrossrefProvider(BaseCitationProvider):
    """Adapter for querying Crossref REST API for scholarly publication metadata."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 8.0,
        user_agent: str | None = None,
    ) -> None:
        settings = get_settings()
        default_url = getattr(settings, "CROSSREF_BASE_URL", "https://api.crossref.org")
        self.base_url: str = str(base_url or default_url)
        contact_email = getattr(settings, "ADMIN_EMAIL", "jarvis-os@users.noreply.github.com")
        self.user_agent = user_agent or f"JARVIS-OS/1.0 (mailto:{contact_email})"
        self.timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "crossref"

    def _parse_crossref_work(self, item: dict[str, Any]) -> CitationRecord | None:
        """Parse raw Crossref work item into canonical CitationRecord."""
        raw_doi = item.get("DOI")
        clean_doi = normalize_doi(raw_doi) if raw_doi else None
        if not clean_doi and not item.get("title"):
            return None

        # Titles are lists in Crossref
        titles = item.get("title", [])
        title = titles[0] if titles and isinstance(titles, list) else str(item.get("title", ""))
        title = " ".join(title.strip().split())

        # Authors
        authors: list[CitationAuthor] = []
        for a in item.get("author", []):
            if isinstance(a, dict):
                given = a.get("given", "").strip()
                family = a.get("family", "").strip()
                name = a.get("name", "").strip()  # Organizational author
                full = name or f"{given} {family}".strip()
                authors.append(CitationAuthor(family=family or name, given=given, full_name=full))

        # Publication date & year
        pub_info = (
            item.get("published-print") or item.get("published-online") or item.get("issued") or {}
        )
        raw_parts = pub_info.get("date-parts", [[]])
        date_parts = raw_parts[0] if raw_parts and len(raw_parts) > 0 else []
        valid_parts = [p for p in date_parts if p is not None and str(p).isdigit()]
        year = int(valid_parts[0]) if valid_parts else None
        date_str = "-".join(f"{int(p):02d}" for p in valid_parts) if valid_parts else None

        # Container / venue
        containers = item.get("container-title", [])
        venue = (
            containers[0] if containers and isinstance(containers, list) else item.get("publisher")
        )

        canonical_url = item.get("resource", {}).get("primary", {}).get("URL") or item.get("URL")

        identifiers: dict[str, str] = {}
        if clean_doi:
            identifiers["doi"] = clean_doi

        return CitationRecord(
            source_id=f"crossref:{clean_doi or title[:40]}",
            doi=clean_doi,
            canonical_url=canonical_url,
            title=title,
            authors=authors,
            publication_date=date_str,
            publication_year=year,
            venue=venue,
            identifiers=identifiers,
            provider=self.provider_name,
            provenance="Crossref REST API works resolution",
            metadata_confidence=0.98,
            metadata_digest=compute_metadata_digest(item),
        )

    async def resolve_by_doi(self, doi: str) -> CitationRecord | None:
        clean_doi = normalize_doi(doi)
        if not clean_doi:
            return None

        url = f"{self.base_url.rstrip('/')}/works/{clean_doi}"
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    item = data.get("message", {})
                    return self._parse_crossref_work(item)
                logger.debug("crossref_doi_lookup_non_200", doi=clean_doi, status=resp.status_code)
                return None
        except Exception as exc:
            logger.warning("crossref_doi_lookup_failed", doi=clean_doi, error=str(exc))
            return None

    async def resolve_by_pmid(self, pmid: str) -> CitationRecord | None:
        # Crossref does not directly key by PMID; delegate to query
        results = await self.search(f"pmid:{pmid}", limit=1)
        return results[0] if results else None

    async def search(self, query: str, limit: int = 3) -> list[CitationRecord]:
        clean_q = query.strip()
        if not clean_q:
            return []

        url = f"{self.base_url.rstrip('/')}/works"
        params: dict[str, str | int] = {"query": clean_q, "rows": min(limit, 10)}
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    items = resp.json().get("message", {}).get("items", [])
                    records: list[CitationRecord] = []
                    for it in items:
                        rec = self._parse_crossref_work(it)
                        if rec:
                            records.append(rec)
                    return records
                return []
        except Exception as exc:
            logger.warning("crossref_search_failed", query=clean_q, error=str(exc))
            return []

    async def resolve_by_url(self, url: str) -> CitationRecord | None:
        doi = normalize_doi(url)
        if doi:
            return await self.resolve_by_doi(doi)
        return None
