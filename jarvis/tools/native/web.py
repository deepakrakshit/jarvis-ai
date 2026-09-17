"""JARVIS Native Web Fetch Tool.

Provides safe URL content fetching with timeout and response size bounding.
"""

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def html_to_readable_text(raw_html: str) -> str:
    """Convert HTML content into clean readable plain text with preserved structure."""
    if not raw_html:
        return ""
    # Strip script, style, and navigation blocks
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<svg[\s\S]*?</svg>", " ", text, flags=re.I)
    # Convert headers, paragraphs, and list items to structured newlines
    text = re.sub(r"<(?:h[1-6]|p|div|section|article)[^>]*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n* ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Unescape HTML entities
    text = html.unescape(text)
    # Collapse excessive blank lines
    clean_lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(clean_lines)


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
            raw_text = content_bytes.decode("utf-8", errors="replace")
            clean_text = html_to_readable_text(raw_text)
            return {
                "url": url,
                "status_code": status_code,
                "body": clean_text,
                "text": clean_text,
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


def extract_detected_year(text: str) -> int | None:
    """Extract a 4-digit publication year (1950-2030) from title or snippet text."""
    matches = re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", text)
    if matches:
        try:
            return int(matches[0])
        except ValueError:
            return None
    return None


def clean_search_url(raw_url: str) -> str:
    """Extract and unquote destination URL from search engine redirect wrapper."""
    if "uddg=" in raw_url:
        try:
            parsed = urllib.parse.urlparse(raw_url)
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("uddg"):
                return urllib.parse.unquote(qs["uddg"][0])
        except Exception:
            pass
    return raw_url


def search_web(
    query: str,
    max_results: int = 5,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    """Perform a live web search and return structured snippets, direct URLs, and detected publication dates."""
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
            raw_url = items[i][0]
            clean_url = clean_search_url(raw_url)
            raw_snippet = (
                html.unescape(re.sub(r"<[^>]+>", "", snippets[i])).strip()
                if i < len(snippets)
                else ""
            )
            detected_year = extract_detected_year(f"{raw_snippet} {raw_title}")
            results.append(
                {
                    "title": raw_title,
                    "url": clean_url,
                    "snippet": raw_snippet,
                    "detected_year": detected_year,
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
