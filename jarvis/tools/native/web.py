"""JARVIS Native Web Fetch Tool.

Provides safe URL content fetching with timeout and response size bounding.
"""

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def fetch_url(url: str, timeout_seconds: float = 10.0, max_bytes: int = 50000) -> dict[str, Any]:
    """Fetch content from an HTTP or HTTPS URL."""
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"Invalid URL protocol: '{url}'. Only http:// and https:// are permitted.")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "JARVIS-OS/1.0 (+https://github.com/deepakrakshit/jarvis-ai)"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            status_code = response.getcode()
            content_bytes = response.read(max_bytes)
            text_content = content_bytes.decode("utf-8", errors="replace")
            return {
                "url": url,
                "status_code": status_code,
                "body": text_content,
                "truncated": len(content_bytes) >= max_bytes,
            }
    except urllib.error.HTTPError as err:
        return {
            "url": url,
            "status_code": err.code,
            "error": str(err.reason),
            "body": "",
        }
    except Exception as exc:
        raise ConnectionError(f"Failed to fetch URL '{url}': {exc}") from exc


def search_web(
    query: str,
    max_results: int = 5,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    """Perform a live web search and return structured snippets and URLs."""
    cleaned_query = query.strip()
    if not cleaned_query:
        return {"query": query, "results": [], "count": 0, "error": "Query cannot be empty"}

    post_data = urllib.parse.urlencode({"q": cleaned_query}).encode("utf-8")
    req = urllib.request.Request(
        "https://lite.duckduckgo.com/lite/",
        data=post_data,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            page_html = response.read().decode("utf-8", errors="replace")

        # Parse links and snippets from DuckDuckGo Lite
        items = re.findall(
            r"<a[^>]+href=[\'\"]([^\'\"]+)[\'\"][^>]*class=[\'\"]result-link[\'\"][^>]*>(.*?)</a>",
            page_html,
            re.DOTALL | re.I,
        )
        snippets = re.findall(
            r"<td[^>]+class=[\'\"]result-snippet[\'\"][^>]*>(.*?)</td>",
            page_html,
            re.DOTALL | re.I,
        )

        results = []
        for i in range(min(max_results, len(items))):
            raw_title = html.unescape(re.sub(r"<[^>]+>", "", items[i][1])).strip()
            url = items[i][0]
            raw_snippet = (
                html.unescape(re.sub(r"<[^>]+>", "", snippets[i])).strip()
                if i < len(snippets)
                else ""
            )
            results.append(
                {
                    "title": raw_title,
                    "url": url,
                    "snippet": raw_snippet,
                }
            )

        return {
            "query": cleaned_query,
            "results": results,
            "count": len(results),
        }
    except Exception as exc:
        return {
            "query": cleaned_query,
            "results": [],
            "count": 0,
            "error": f"Search failed: {exc}",
        }
