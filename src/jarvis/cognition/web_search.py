"""Web Search Capability for JARVIS Cognition & Real-Time Models.

Provides fast, resilient real-time internet search capabilities via DuckDuckGo
and configurable HTTP search providers, returning structured titles, snippets,
and URLs for model grounding.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from jarvis.config import settings
from jarvis.telemetry import logger


@dataclass
class SearchResultItem:
    """Structured result item for search outputs."""

    title: str
    snippet: str
    url: str

    def to_dict(self) -> Dict[str, str]:
        """Convert search result item to a dictionary."""
        return {
            "title": self.title,
            "snippet": self.snippet,
            "url": self.url,
        }


class WebSearchClient:
    """Performs real-time web queries with resilience and fallback handling."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        timeout: Optional[float] = None,
        max_results: Optional[int] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        self.endpoint = endpoint or settings.WEB_SEARCH_ENDPOINT
        self.timeout = timeout or settings.WEB_SEARCH_TIMEOUT_SECONDS
        self.max_results = max_results or settings.WEB_SEARCH_MAX_RESULTS
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

    async def search(
        self,
        query: str,
        max_results: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        """Search the web for query and return structured search results."""
        clean_query = query.strip()
        if not clean_query:
            return []

        limit = max_results or self.max_results
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            async with httpx.AsyncClient(
                headers=headers,
                follow_redirects=True,
                timeout=self.timeout,
            ) as client:
                # 1. Primary: DuckDuckGo HTML Search
                response = await client.post(
                    self.endpoint,
                    data={"q": clean_query},
                )
                if response.is_success:
                    html_results = self._parse_ddg_html(response.text, limit=limit)
                    if html_results:
                        return html_results

                # 2. Fallback: DuckDuckGo Instant Answer / Topics API
                api_response = await client.get(
                    "https://api.duckduckgo.com/",
                    params={
                        "q": clean_query,
                        "format": "json",
                        "no_html": "1",
                        "skip_disambig": "1",
                    },
                )
                if api_response.is_success:
                    return self._parse_ddg_api(api_response.text, query=clean_query, limit=limit)

                return []

        except httpx.TimeoutException:
            logger.warning(f"Web search timed out after {self.timeout}s for query: '{clean_query}'")
            return []
        except Exception as err:
            logger.error(f"Error during web search for '{clean_query}': {err}", exc_info=True)
            return []

    def _parse_ddg_api(self, json_text: str, query: str, limit: int) -> List[Dict[str, str]]:
        """Extract results from DuckDuckGo Instant Answer API response."""
        import json

        results: List[Dict[str, str]] = []
        try:
            data = json.loads(json_text)
        except Exception:
            return results

        if data.get("AbstractText"):
            results.append(
                SearchResultItem(
                    title=data.get("Heading") or query,
                    snippet=data.get("AbstractText", ""),
                    url=data.get("AbstractURL", ""),
                ).to_dict()
            )

        for topic in data.get("RelatedTopics", []):
            if isinstance(topic, dict) and "Text" in topic:
                raw_url = topic.get("FirstURL", "")
                title = raw_url.split("/")[-1].replace("_", " ") if raw_url else query
                results.append(
                    SearchResultItem(
                        title=title,
                        snippet=topic.get("Text", ""),
                        url=raw_url,
                    ).to_dict()
                )
                if len(results) >= limit:
                    break

        return results

    def _parse_ddg_html(self, html: str, limit: int) -> List[Dict[str, str]]:
        """Extract titles, snippets, and URLs from DuckDuckGo HTML response."""
        soup = BeautifulSoup(html, "html.parser")
        results: List[Dict[str, str]] = []

        for item in soup.select(".result"):
            title_el = item.select_one(".result__title")
            snippet_el = item.select_one(".result__snippet")
            url_el = item.select_one(".result__url")

            if title_el and snippet_el:
                title = title_el.get_text(strip=True)
                snippet = snippet_el.get_text(strip=True)
                url = url_el.get_text(strip=True) if url_el else ""

                results.append(
                    SearchResultItem(
                        title=title,
                        snippet=snippet,
                        url=url,
                    ).to_dict()
                )

                if len(results) >= limit:
                    break

        return results


# Global singleton client instance
web_search_client = WebSearchClient()


async def search_web(
    query: str,
    max_results: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Execute a real-time web search and return structured result snippets."""
    return await web_search_client.search(query=query, max_results=max_results)
