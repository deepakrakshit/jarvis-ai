"""Authoritative bibliographic metadata provider adapters."""

from jarvis.core.verification.providers.base import (
    BaseCitationProvider,
    compute_metadata_digest,
    normalize_doi,
)
from jarvis.core.verification.providers.crossref import CrossrefProvider
from jarvis.core.verification.providers.doi_negotiation import DoiNegotiationProvider
from jarvis.core.verification.providers.openalex import OpenAlexProvider
from jarvis.core.verification.providers.publisher_html import PublisherHtmlProvider
from jarvis.core.verification.providers.pubmed import PubMedProvider

__all__ = [
    "BaseCitationProvider",
    "CrossrefProvider",
    "DoiNegotiationProvider",
    "OpenAlexProvider",
    "PubMedProvider",
    "PublisherHtmlProvider",
    "compute_metadata_digest",
    "normalize_doi",
]
