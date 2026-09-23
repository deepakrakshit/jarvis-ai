"""Tests for real-time web search client and parser."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from jarvis.cognition.web_search import WebSearchClient, search_web


@pytest.mark.asyncio
async def test_web_search_empty_query() -> None:
    """Verify search with empty or whitespace string immediately returns empty list."""
    client = WebSearchClient()
    results = await client.search("   ")
    assert results == []


@pytest.mark.asyncio
async def test_web_search_html_parsing() -> None:
    """Verify DuckDuckGo HTML response correctly parses titles, snippets, and URLs."""
    sample_html = """
    <html>
      <body>
        <div class="result">
          <a class="result__title" href="https://python.org">Python Programming Language</a>
          <a class="result__snippet">Python is a high-level, general-purpose programming language.</a>
          <span class="result__url">https://python.org</span>
        </div>
        <div class="result">
          <a class="result__title" href="https://docs.python.org">Python Documentation</a>
          <a class="result__snippet">Official documentation and guides for Python 3.</a>
          <span class="result__url">https://docs.python.org</span>
        </div>
      </body>
    </html>
    """

    client = WebSearchClient()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = sample_html

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        results = await client.search("python programming", max_results=5)

        assert len(results) == 2
        assert results[0]["title"] == "Python Programming Language"
        assert "high-level" in results[0]["snippet"]
        assert results[0]["url"] == "https://python.org"
        assert results[1]["title"] == "Python Documentation"


@pytest.mark.asyncio
async def test_web_search_timeout_handling() -> None:
    """Verify timeout is caught and returns empty list rather than crashing."""
    client = WebSearchClient(timeout=0.1)

    with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Search timed out")):
        results = await client.search("query that times out")
        assert results == []


@pytest.mark.asyncio
async def test_web_search_http_error_handling() -> None:
    """Verify non-200 status code returns empty list gracefully."""
    client = WebSearchClient()
    mock_response = AsyncMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        results = await client.search("query with 503")
        assert results == []


@pytest.mark.asyncio
async def test_search_web_convenience_function() -> None:
    """Verify search_web convenience wrapper functions as expected."""
    with patch.object(WebSearchClient, "search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [
            {"title": "Test Title", "snippet": "Test Snippet", "url": "https://example.com"}
        ]
        res = await search_web("test query")
        assert len(res) == 1
        assert res[0]["title"] == "Test Title"
