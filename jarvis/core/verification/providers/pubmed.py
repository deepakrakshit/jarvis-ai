"""PubMed / NCBI E-utilities authoritative biomedical metadata provider adapter."""

import re
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


class PubMedProvider(BaseCitationProvider):
    """Adapter for querying NCBI Entrez E-utilities for biomedical literature metadata."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        settings = get_settings()
        default_url = getattr(
            settings, "PUBMED_EUTILS_BASE_URL", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        )
        self.base_url: str = str(base_url or default_url)
        self.timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "pubmed"

    def _parse_pubmed_docsum(self, docsum: dict[str, Any], pmid: str) -> CitationRecord | None:
        """Parse NCBI docsum JSON object into canonical CitationRecord."""
        raw_title = docsum.get("title", "")
        clean_title = re.sub(r"<[^>]+>", "", raw_title).rstrip(".")
        clean_title = " ".join(clean_title.strip().split())

        if not clean_title:
            return None

        authors: list[CitationAuthor] = []
        for a in docsum.get("authors", []):
            name = a.get("name", "").strip()
            if name:
                parts = name.split()
                family = parts[0] if parts else name
                given = " ".join(parts[1:]) if len(parts) > 1 else ""
                authors.append(CitationAuthor(family=family, given=given, full_name=name))

        # Year from pubdate
        pubdate = docsum.get("pubdate", "")
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", pubdate)
        year = int(year_match.group(1)) if year_match else None

        venue = docsum.get("source")

        # Extract DOI from articleids
        clean_doi = None
        for aid in docsum.get("articleids", []):
            if aid.get("idtype") == "doi":
                clean_doi = normalize_doi(aid.get("value"))
                break

        identifiers: dict[str, str] = {"pmid": str(pmid)}
        if clean_doi:
            identifiers["doi"] = clean_doi

        canonical_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        return CitationRecord(
            source_id=f"pubmed:{pmid}",
            doi=clean_doi,
            pmid=str(pmid),
            canonical_url=canonical_url,
            title=clean_title,
            authors=authors,
            publication_date=pubdate or (str(year) if year else None),
            publication_year=year,
            venue=venue,
            identifiers=identifiers,
            provider=self.provider_name,
            provenance="NCBI Entrez E-utilities (esearch + esummary)",
            metadata_confidence=0.98,
            metadata_digest=compute_metadata_digest(docsum),
        )

    async def resolve_by_pmid(self, pmid: str) -> CitationRecord | None:
        clean_pmid = str(pmid).strip()
        if not clean_pmid.isdigit():
            return None

        url = f"{self.base_url.rstrip('/')}/esummary.fcgi"
        params = {"db": "pubmed", "id": clean_pmid, "retmode": "json"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    result_dict = data.get("result", {})
                    docsum = result_dict.get(clean_pmid)
                    if isinstance(docsum, dict):
                        return self._parse_pubmed_docsum(docsum, clean_pmid)
                return None
        except Exception as exc:
            logger.warning("pubmed_summary_failed", pmid=clean_pmid, error=str(exc))
            return None

    async def resolve_by_doi(self, doi: str) -> CitationRecord | None:
        clean_doi = normalize_doi(doi)
        if not clean_doi:
            return None

        search_url = f"{self.base_url.rstrip('/')}/esearch.fcgi"
        params = {"db": "pubmed", "term": f"{clean_doi}[aid]", "retmode": "json"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(search_url, params=params)
                if resp.status_code == 200:
                    idlist = resp.json().get("esearchresult", {}).get("idlist", [])
                    if idlist:
                        return await self.resolve_by_pmid(idlist[0])
                return None
        except Exception as exc:
            logger.warning("pubmed_doi_search_failed", doi=clean_doi, error=str(exc))
            return None

    async def search(self, query: str, limit: int = 3) -> list[CitationRecord]:
        clean_q = query.strip()
        if not clean_q:
            return []

        search_url = f"{self.base_url.rstrip('/')}/esearch.fcgi"
        params: dict[str, str | int] = {
            "db": "pubmed",
            "term": clean_q,
            "retmode": "json",
            "retmax": min(limit, 5),
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(search_url, params=params)
                if resp.status_code == 200:
                    idlist = resp.json().get("esearchresult", {}).get("idlist", [])
                    records: list[CitationRecord] = []
                    for pmid in idlist:
                        rec = await self.resolve_by_pmid(pmid)
                        if rec:
                            records.append(rec)
                    return records
                return []
        except Exception as exc:
            logger.warning("pubmed_query_search_failed", query=clean_q, error=str(exc))
            return []

    async def resolve_by_url(self, url: str) -> CitationRecord | None:
        # Check if URL contains pubmed ID
        pmid_match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
        if pmid_match:
            return await self.resolve_by_pmid(pmid_match.group(1))
        doi = normalize_doi(url)
        if doi:
            return await self.resolve_by_doi(doi)
        return None
