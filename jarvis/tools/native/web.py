"""JARVIS Native Web Fetch Tool.

Provides safe URL content fetching with timeout and response size bounding.
"""

import urllib.error
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
