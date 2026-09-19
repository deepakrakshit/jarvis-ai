"""Playwright Browser Management & Execution Session for JARVIS.

Enforces Section 19 of ARCHITECTURE.md:
Preferred control order:
1. Structured DOM operations & content extraction first
2. Clean text / accessibility snapshots before screenshots
3. Safe click and type interactions
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from playwright.async_api import Browser, Page, Playwright, async_playwright

from jarvis.config import settings
from jarvis.telemetry import logger


class BrowserManager:
    """Async Playwright browser session manager."""

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()

    async def _ensure_page(self) -> Page:
        """Lazily initialize Playwright browser and primary page."""
        if self._page is not None and not self._page.is_closed():
            return self._page

        logger.info("Initializing Playwright Chromium browser session...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--no-sandbox"],
        )
        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 JARVIS/3.0",
        )
        self._page = await context.new_page()
        return self._page

    async def navigate(self, url: str) -> Dict[str, Any]:
        """Navigate to a target URL and wait for DOM content loaded."""
        async with self._lock:
            page = await self._ensure_page()
            logger.info(f"Navigating browser to {url}")
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = await page.title()
            status_code = response.status if response else 200

            return {
                "action": "navigate",
                "url": page.url,
                "title": title,
                "status_code": status_code,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def snapshot(self, max_chars: int = 20000) -> Dict[str, Any]:
        """Capture structured text representation of the current page."""
        async with self._lock:
            page = await self._ensure_page()
            title = await page.title()
            current_url = page.url

            # Extract body innerText
            try:
                text_content = await page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )
            except Exception:
                text_content = ""

            cleaned_text = " ".join(text_content.split())
            if len(cleaned_text) > max_chars:
                cleaned_text = cleaned_text[:max_chars] + "... [truncated]"

            return {
                "action": "snapshot",
                "url": current_url,
                "title": title,
                "content_length": len(cleaned_text),
                "text": cleaned_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def click(self, selector: str) -> Dict[str, Any]:
        """Click an element matching the given CSS, XPath, or text selector."""
        async with self._lock:
            page = await self._ensure_page()
            logger.info(f"Clicking browser element matching selector: '{selector}'")
            await page.click(selector, timeout=10000)
            return {
                "action": "click",
                "selector": selector,
                "current_url": page.url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def type_text(self, selector: str, text: str) -> Dict[str, Any]:
        """Fill or type text into an input element."""
        async with self._lock:
            page = await self._ensure_page()
            logger.info(f"Typing into browser element '{selector}' (length {len(text)})")
            await page.fill(selector, text, timeout=10000)
            return {
                "action": "type",
                "selector": selector,
                "text_length": len(text),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def screenshot(self, save_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Capture a viewport screenshot and save as an artifact."""
        async with self._lock:
            page = await self._ensure_page()
            target_dir = save_dir or settings.ARTIFACTS_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = f"browser_{uuid4().hex[:8]}.png"
            filepath = target_dir / filename

            logger.info(f"Capturing browser screenshot to {filepath}")
            await page.screenshot(path=str(filepath))

            return {
                "action": "screenshot",
                "artifact_path": str(filepath),
                "filename": filename,
                "url": page.url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def close(self) -> None:
        """Close browser resources cleanly."""
        async with self._lock:
            if self._page and not self._page.is_closed():
                await self._page.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self._page = None
            self._browser = None
            self._playwright = None
            logger.info("Browser session closed cleanly.")


# Global singleton instance
browser_manager = BrowserManager()
