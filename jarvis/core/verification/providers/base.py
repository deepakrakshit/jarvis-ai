"""Base interfaces and utilities for external authoritative citation metadata providers."""

import hashlib
import json
import re
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any

from jarvis.core.verification.records import CitationRecord


def normalize_doi(raw_doi_or_url: str | None) -> str | None:
    """Extract and normalize a clean lowercase DOI string from raw text or URLs."""
    if not raw_doi_or_url:
        return None
    # Unquote URL encoding if present
    decoded = urllib.parse.unquote(raw_doi_or_url).strip()
    match = re.search(r"\b(10\.\d{4,9}/[^\s\"\'<>]+)", decoded, re.IGNORECASE)
    if match:
        clean = match.group(1).strip().rstrip(".,;:/()[]")
        return clean.lower()

    # Recognize Nature article URLs e.g. /articles/s41591-025-03983-2 -> 10.1038/s41591-025-03983-2
    nature_match = re.search(
        r"nature\.com/articles/([a-z]\d{5}-\d{3}-\d{5}-\w)", decoded, re.IGNORECASE
    )
    if nature_match:
        return f"10.1038/{nature_match.group(1).lower()}"

    # Recognize Lancet PII URLs e.g. /article/PIIS2589-7500(24)00047-5 -> 10.1016/S2589-7500(24)00047-5
    lancet_match = re.search(
        r"thelancet\.com/journals/\w+/article/PII([A-Z0-9\(\)\-]+)", decoded, re.IGNORECASE
    )
    if lancet_match:
        return f"10.1016/{lancet_match.group(1).lower()}"

    return None


def compute_metadata_digest(data: Any) -> str:
    """Compute deterministic SHA256 hex digest for raw provider metadata."""
    try:
        serialized = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    except Exception:
        serialized = str(data).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


class BaseCitationProvider(ABC):
    """Abstract provider adapter for authoritative bibliographic metadata sources."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the external metadata provider."""
        ...

    @abstractmethod
    async def resolve_by_doi(self, doi: str) -> CitationRecord | None:
        """Resolve a publication by DOI into a canonical CitationRecord."""
        ...

    @abstractmethod
    async def resolve_by_pmid(self, pmid: str) -> CitationRecord | None:
        """Resolve a publication by PubMed ID into a canonical CitationRecord."""
        ...

    @abstractmethod
    async def search(self, query: str, limit: int = 3) -> list[CitationRecord]:
        """Search for publications matching a query string."""
        ...

    @abstractmethod
    async def resolve_by_url(self, url: str) -> CitationRecord | None:
        """Resolve publication metadata directly from a publisher URL."""
        ...
