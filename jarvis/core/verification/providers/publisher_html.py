"""Publisher Canonical HTML metadata provider adapter."""

import html
import re

import httpx

from jarvis.core.logging import get_logger
from jarvis.core.verification.providers.base import (
    BaseCitationProvider,
    compute_metadata_digest,
    normalize_doi,
)
from jarvis.core.verification.records import CitationAuthor, CitationRecord

logger = get_logger(__name__)


class PublisherHtmlProvider(BaseCitationProvider):
    """Adapter extracting Highwire Press, Dublin Core, and OpenGraph metadata from publisher pages."""

    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "publisher_html"

    def _extract_meta_content(self, html_text: str, name_pattern: str) -> list[str]:
        """Extract all content values for meta tags matching name or property pattern."""
        regex = rf"<meta\s+[^>]*(?:name|property)=[\'\"]({name_pattern})[\'\"][^>]*content=[\'\"](.*?)[\'\"]"
        matches1 = re.findall(regex, html_text, re.IGNORECASE)
        regex_rev = rf"<meta\s+[^>]*content=[\'\"](.*?)[\'\"][^>]*(?:name|property)=[\'\"]({name_pattern})[\'\"]"
        matches2 = re.findall(regex_rev, html_text, re.IGNORECASE)

        results: list[str] = []
        for _, val in matches1:
            results.append(html.unescape(val).strip())
        for val, _ in matches2:
            results.append(html.unescape(val).strip())
        return results

    def _parse_html_metadata(self, html_text: str, url: str) -> CitationRecord | None:
        """Extract structured CitationRecord from academic publisher meta tags."""
        # 1. Title
        titles = self._extract_meta_content(
            html_text, r"citation_title|dc\.title|dcterms\.title|og:title"
        )
        title = titles[0] if titles else ""
        if not title:
            # Fallback to <title> tag
            t_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
            if t_match:
                title = re.sub(r"<[^>]+>", "", t_match.group(1)).strip()
        title = " ".join(title.split())
        if not title or title.lower() in (
            "redirecting",
            "redirect",
            "loading...",
            "just a moment...",
            "cloudflare",
            "access denied",
            "robot check",
            "security check",
        ):
            return None

        # 2. Authors
        author_names = self._extract_meta_content(
            html_text, r"citation_author|dc\.creator|dcterms\.creator|author"
        )
        authors: list[CitationAuthor] = []
        for raw_a in author_names:
            clean = " ".join(raw_a.split())
            if clean:
                parts = clean.split()
                family = parts[-1] if len(parts) > 1 else clean
                given = " ".join(parts[:-1]) if len(parts) > 1 else ""
                authors.append(CitationAuthor(family=family, given=given, full_name=clean))

        # 3. DOI
        dois = self._extract_meta_content(
            html_text, r"citation_doi|dc\.identifier|dc\.identifier\.doi"
        )
        clean_doi = None
        for d in dois:
            norm = normalize_doi(d)
            if norm:
                clean_doi = norm
                break
        if not clean_doi:
            clean_doi = normalize_doi(url)

        # 4. Publication date & year
        dates = self._extract_meta_content(
            html_text,
            r"citation_publication_date|citation_date|dc\.date|dcterms\.date|citation_online_date",
        )
        date_str = dates[0] if dates else None
        year = None
        if date_str:
            y_match = re.search(r"\b(19\d{2}|20\d{2})\b", date_str)
            if y_match:
                year = int(y_match.group(1))

        # 5. Venue / Journal
        venues = self._extract_meta_content(
            html_text, r"citation_journal_title|citation_publisher|dc\.publisher"
        )
        venue = venues[0] if venues else None

        identifiers: dict[str, str] = {}
        if clean_doi:
            identifiers["doi"] = clean_doi

        return CitationRecord(
            source_id=f"publisher_html:{clean_doi or url}",
            doi=clean_doi,
            canonical_url=url,
            title=title,
            authors=authors,
            publication_date=date_str,
            publication_year=year,
            venue=venue,
            identifiers=identifiers,
            provider=self.provider_name,
            provenance=f"Publisher HTML metadata tags from {url}",
            metadata_confidence=0.92,
            metadata_digest=compute_metadata_digest(
                {
                    "title": title,
                    "doi": clean_doi,
                    "authors": [a.model_dump() for a in authors],
                    "year": year,
                }
            ),
        )

    async def resolve_by_url(self, url: str) -> CitationRecord | None:
        if not (url.startswith("http://") or url.startswith("https://")):
            return None

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return self._parse_html_metadata(resp.text, str(resp.url))
                return None
        except Exception as exc:
            logger.warning("publisher_html_fetch_failed", url=url, error=str(exc))
            return None

    async def resolve_by_doi(self, doi: str) -> CitationRecord | None:
        clean_doi = normalize_doi(doi)
        if not clean_doi:
            return None
        return await self.resolve_by_url(f"https://doi.org/{clean_doi}")

    async def resolve_by_pmid(self, pmid: str) -> CitationRecord | None:
        return await self.resolve_by_url(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")

    async def search(self, query: str, limit: int = 3) -> list[CitationRecord]:
        return []
