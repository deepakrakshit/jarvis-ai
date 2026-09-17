"""OpenAlex scholarly graph API authoritative metadata provider adapter."""

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


class OpenAlexProvider(BaseCitationProvider):
    """Adapter for querying OpenAlex API for scholarly works metadata."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 8.0,
        user_agent: str | None = None,
    ) -> None:
        settings = get_settings()
        default_url = getattr(settings, "OPENALEX_BASE_URL", "https://api.openalex.org")
        self.base_url: str = str(base_url or default_url)
        contact_email = getattr(settings, "ADMIN_EMAIL", "jarvis-os@users.noreply.github.com")
        self.user_agent = user_agent or f"JARVIS-OS/1.0 (mailto:{contact_email})"
        self.timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "openalex"

    def _parse_openalex_work(self, item: dict[str, Any]) -> CitationRecord | None:
        """Parse raw OpenAlex work item into canonical CitationRecord."""
        raw_doi = item.get("doi")
        clean_doi = normalize_doi(raw_doi)
        title = item.get("title") or item.get("display_name") or ""
        title = " ".join(str(title).strip().split())
        if not clean_doi and not title:
            return None

        # Authors from authorships
        authors: list[CitationAuthor] = []
        for authorship in item.get("authorships", []):
            author_obj = authorship.get("author", {})
            raw_name = author_obj.get("display_name", "").strip()
            if raw_name:
                parts = raw_name.split()
                family = parts[-1] if len(parts) > 1 else raw_name
                given = " ".join(parts[:-1]) if len(parts) > 1 else ""
                authors.append(CitationAuthor(family=family, given=given, full_name=raw_name))

        year = item.get("publication_year")
        date_str = item.get("publication_date")

        primary_loc = item.get("primary_location") or {}
        source = primary_loc.get("source") or {}
        venue = source.get("display_name")

        canonical_url = item.get("doi") or primary_loc.get("landing_page_url")

        identifiers: dict[str, str] = {}
        if clean_doi:
            identifiers["doi"] = clean_doi
        ids = item.get("ids", {})
        if ids.get("pmid"):
            pmid_val = str(ids["pmid"]).replace("https://pubmed.ncbi.nlm.nih.gov/", "")
            identifiers["pmid"] = pmid_val
        if ids.get("openalex"):
            identifiers["openalex"] = str(ids["openalex"])

        return CitationRecord(
            source_id=f"openalex:{clean_doi or item.get('id') or title[:40]}",
            doi=clean_doi,
            pmid=identifiers.get("pmid"),
            canonical_url=canonical_url,
            title=title,
            authors=authors,
            publication_date=date_str,
            publication_year=int(year) if year else None,
            venue=venue,
            identifiers=identifiers,
            provider=self.provider_name,
            provenance="OpenAlex Scholarly Graph API",
            metadata_confidence=0.96,
            metadata_digest=compute_metadata_digest(item),
        )

    async def resolve_by_doi(self, doi: str) -> CitationRecord | None:
        clean_doi = normalize_doi(doi)
        if not clean_doi:
            return None

        url = f"{self.base_url.rstrip('/')}/works/https://doi.org/{clean_doi}"
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return self._parse_openalex_work(data)
                logger.debug("openalex_doi_lookup_non_200", doi=clean_doi, status=resp.status_code)
                return None
        except Exception as exc:
            logger.warning("openalex_doi_lookup_failed", doi=clean_doi, error=str(exc))
            return None

    async def resolve_by_pmid(self, pmid: str) -> CitationRecord | None:
        url = f"{self.base_url.rstrip('/')}/works/pmid:{pmid}"
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return self._parse_openalex_work(resp.json())
                return None
        except Exception as exc:
            logger.warning("openalex_pmid_lookup_failed", pmid=pmid, error=str(exc))
            return None

    async def search(self, query: str, limit: int = 3) -> list[CitationRecord]:
        clean_q = query.strip()
        if not clean_q:
            return []

        url = f"{self.base_url.rstrip('/')}/works"
        params: dict[str, str | int] = {"search": clean_q, "per_page": min(limit, 10)}
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    records: list[CitationRecord] = []
                    for it in results:
                        rec = self._parse_openalex_work(it)
                        if rec:
                            records.append(rec)
                    return records
                return []
        except Exception as exc:
            logger.warning("openalex_search_failed", query=clean_q, error=str(exc))
            return []

    async def resolve_by_url(self, url: str) -> CitationRecord | None:
        doi = normalize_doi(url)
        if doi:
            return await self.resolve_by_doi(doi)
        return None
