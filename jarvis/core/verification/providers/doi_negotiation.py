"""DOI Content Negotiation metadata provider adapter."""

from typing import Any

import httpx

from jarvis.core.logging import get_logger
from jarvis.core.verification.providers.base import (
    BaseCitationProvider,
    compute_metadata_digest,
    normalize_doi,
)
from jarvis.core.verification.records import CitationAuthor, CitationRecord

logger = get_logger(__name__)


class DoiNegotiationProvider(BaseCitationProvider):
    """Adapter for resolving DOIs via standard HTTP Content Negotiation (CSL-JSON)."""

    def __init__(
        self,
        resolver_url: str = "https://doi.org",
        timeout_seconds: float = 8.0,
    ) -> None:
        self.resolver_url = resolver_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "doi_negotiation"

    def _parse_csl_json(self, data: dict[str, Any], raw_doi: str) -> CitationRecord | None:
        """Parse CSL-JSON dictionary into canonical CitationRecord."""
        title = data.get("title", "")
        if isinstance(title, list):
            title = title[0] if title else ""
        title = " ".join(str(title).strip().split())

        clean_doi = normalize_doi(data.get("DOI") or raw_doi)

        authors: list[CitationAuthor] = []
        for a in data.get("author", []):
            if isinstance(a, dict):
                family = str(a.get("family", "")).strip()
                given = str(a.get("given", "")).strip()
                literal = str(a.get("literal", "")).strip()
                full = literal or f"{given} {family}".strip()
                authors.append(
                    CitationAuthor(family=family or literal, given=given, full_name=full)
                )

        # Year and date from issued or published
        issued = data.get("issued") or data.get("published") or {}
        date_parts = issued.get("date-parts", [[]])[0] if isinstance(issued, dict) else []
        year = int(date_parts[0]) if date_parts and len(date_parts) > 0 else None
        date_str = "-".join(f"{p:02d}" for p in date_parts) if date_parts else None

        venue = data.get("container-title") or data.get("publisher")
        if isinstance(venue, list):
            venue = venue[0] if venue else None

        canonical_url = data.get("URL") or (f"https://doi.org/{clean_doi}" if clean_doi else None)

        identifiers: dict[str, str] = {}
        if clean_doi:
            identifiers["doi"] = clean_doi
        if data.get("PMID"):
            identifiers["pmid"] = str(data["PMID"])

        return CitationRecord(
            source_id=f"doi_negotiation:{clean_doi or title[:40]}",
            doi=clean_doi,
            canonical_url=canonical_url,
            title=title,
            authors=authors,
            publication_date=date_str,
            publication_year=year,
            venue=venue,
            identifiers=identifiers,
            provider=self.provider_name,
            provenance="DOI HTTP Content Negotiation (application/vnd.citationstyles.csl+json)",
            metadata_confidence=0.97,
            metadata_digest=compute_metadata_digest(data),
        )

    async def resolve_by_doi(self, doi: str) -> CitationRecord | None:
        clean_doi = normalize_doi(doi)
        if not clean_doi:
            return None

        url = f"{self.resolver_url}/{clean_doi}"
        headers = {
            "Accept": "application/vnd.citationstyles.csl+json, application/json",
            "User-Agent": "JARVIS-OS/1.0",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        return self._parse_csl_json(data, clean_doi)
                    except Exception:
                        return None
                logger.debug("doi_negotiation_non_200", doi=clean_doi, status=resp.status_code)
                return None
        except Exception as exc:
            logger.warning("doi_negotiation_failed", doi=clean_doi, error=str(exc))
            return None

    async def resolve_by_pmid(self, pmid: str) -> CitationRecord | None:
        return None

    async def search(self, query: str, limit: int = 3) -> list[CitationRecord]:
        return []

    async def resolve_by_url(self, url: str) -> CitationRecord | None:
        doi = normalize_doi(url)
        if doi:
            return await self.resolve_by_doi(doi)
        return None
