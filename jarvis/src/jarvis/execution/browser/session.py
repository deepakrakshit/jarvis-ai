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

        logger.info("Initializing Playwright browser session (visible on desktop)...")
        self._playwright = await async_playwright().start()

        headless = getattr(settings, "BROWSER_HEADLESS", False)
        browser_channel = getattr(settings, "BROWSER_CHANNEL", "chrome")
        launch_args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ]

        try:
            self._browser = await self._playwright.chromium.launch(
                channel=browser_channel,
                headless=headless,
                args=launch_args,
            )
            logger.info("Launched system Chrome browser via Playwright.")
        except Exception as exc:
            logger.info(f"System Chrome channel unavailable ({exc}), launching default Chromium...")
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                args=launch_args,
            )

        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 JARVIS/3.0",
        )
        self._page = await context.new_page()
        return self._page

    async def navigate(self, url: str) -> Dict[str, Any]:
        """Navigate to a target URL and wait for initial DOM content."""
        async with self._lock:
            page = await self._ensure_page()
            logger.info(f"Navigating browser to {url}")
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Auto-dismiss cookie/consent dialogs if present
            try:
                consent_btn = page.locator(
                    'button:has-text("Accept all"), button:has-text("Reject all"), button:has-text("I agree"), ytd-button-renderer:has-text("Accept all")'
                )
                if await consent_btn.count() > 0 and await consent_btn.first.is_visible():
                    await consent_btn.first.click(timeout=1500)
            except Exception:
                pass

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
        """Capture structured text and interactive element representation of the current page."""
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

            # Extract key interactive elements (videos, search inputs, major action buttons)
            interactive_summary = ""
            try:
                elements = await page.evaluate("""() => {
                    const items = [];
                    // Top videos on search pages
                    document.querySelectorAll('a#video-title, ytd-video-renderer a#video-title, h3 a').forEach((el, i) => {
                        const txt = el.innerText ? el.innerText.trim() : '';
                        if (i < 8 && txt) {
                            items.push(`[Video: "${txt}" -> selector: "a#video-title"]`);
                        }
                    });
                    // Search inputs
                    document.querySelectorAll('input[name=search_query], input#search, input[type=search]').forEach((el) => {
                        items.push(`[Search Input: selector="input[name=search_query]"]`);
                    });
                    return items.slice(0, 8).join("\\n");
                }""")
                if elements:
                    interactive_summary = f"\\n\\n[Key Interactive Elements]\\n{elements}"
            except Exception:
                pass

            total_content = cleaned_text + interactive_summary
            if len(total_content) > max_chars:
                total_content = total_content[:max_chars] + "... [truncated]"

            return {
                "action": "snapshot",
                "url": current_url,
                "title": title,
                "content_length": len(total_content),
                "text": total_content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def click(self, selector: str) -> Dict[str, Any]:
        """Click an element matching the given selector with intelligent fallback resolution."""
        async with self._lock:
            page = await self._ensure_page()
            logger.info(f"Clicking browser element matching selector: '{selector}'")

            loc = page.locator(selector)
            count = 0
            try:
                count = await loc.count()
            except Exception:
                count = 0

            # Dynamic fallback resolution for common elements
            if count == 0:
                fallbacks = []
                if "video" in selector.lower() or "title" in selector.lower():
                    fallbacks.extend(
                        [
                            "a#video-title",
                            "ytd-video-renderer a#video-title",
                            "#video-title",
                            "h3 a",
                            "ytd-thumbnail a",
                        ]
                    )
                elif "search" in selector.lower():
                    fallbacks.extend(
                        [
                            "button#search-icon-legacy",
                            "button[aria-label='Search']",
                            "button[aria-label*='Search']",
                        ]
                    )

                for fb in fallbacks:
                    fb_loc = page.locator(fb)
                    try:
                        if await fb_loc.count() > 0:
                            loc = fb_loc
                            count = 1
                            break
                    except Exception:
                        pass

            # If waiting for video elements on dynamic SPAs
            if "video" in selector.lower() and count == 0:
                try:
                    await page.wait_for_selector("a#video-title, ytd-video-renderer", timeout=5000)
                    loc = page.locator("a#video-title").first
                except Exception:
                    pass

            await loc.first.click(timeout=10000)

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=4000)
            except Exception:
                pass

            return {
                "action": "click",
                "selector": selector,
                "current_url": page.url,
                "title": await page.title(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def type_text(self, selector: str, text: str, press_enter: bool = True) -> Dict[str, Any]:
        """Fill or type text into an input element and optionally submit."""
        async with self._lock:
            page = await self._ensure_page()
            logger.info(f"Typing into browser element '{selector}' (length {len(text)})")

            target_loc = page.locator(selector)
            count = 0
            try:
                count = await target_loc.count()
            except Exception:
                count = 0

            if count == 0:
                if "search" in selector.lower():
                    fb = page.locator(
                        "input[name='search_query'], input#search, input[type='search'], input"
                    )
                    if await fb.count() > 0:
                        target_loc = fb.first
                else:
                    target_loc = page.locator("input, textarea").first

            await target_loc.first.fill(text, timeout=10000)

            should_enter = press_enter or text.endswith("\n") or ("search" in selector.lower())
            if should_enter:
                await page.keyboard.press("Enter")
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass

            return {
                "action": "type",
                "selector": selector,
                "text": text,
                "text_length": len(text),
                "submitted": should_enter,
                "current_url": page.url,
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
