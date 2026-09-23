"""Browser Node Execution Handler for JARVIS.

Registers and dispatches browser capabilities via Playwright.
"""

from typing import Any

from jarvis.actions.registry import capability_registry
from jarvis.contracts.action import ActionRequest
from jarvis.execution.browser.session import browser_manager
from jarvis.policy.firewall import (
    CAPABILITY_BROWSER_CLICK,
    CAPABILITY_BROWSER_NAVIGATE,
    CAPABILITY_BROWSER_SCREENSHOT,
    CAPABILITY_BROWSER_SNAPSHOT,
    CAPABILITY_BROWSER_TYPE,
)
from jarvis.telemetry import logger


class BrowserNode:
    """Execution node governing browser lifecycle and capabilities."""

    def __init__(self) -> None:
        self._registered = False

    def register_capabilities(self) -> None:
        """Register browser actions into the canonical capability registry."""
        if self._registered:
            return

        logger.info("Registering Browser Node execution capabilities...")

        async def handle_navigate(req: ActionRequest) -> Any:
            return await browser_manager.navigate(url=str(req.arguments["url"]))

        async def handle_snapshot(req: ActionRequest) -> Any:
            return await browser_manager.snapshot(
                max_chars=int(req.arguments.get("max_chars", 20000))
            )

        async def handle_click(req: ActionRequest) -> Any:
            return await browser_manager.click(selector=str(req.arguments["selector"]))

        async def handle_type(req: ActionRequest) -> Any:
            return await browser_manager.type_text(
                selector=str(req.arguments["selector"]),
                text=str(req.arguments["text"]),
                press_enter=bool(req.arguments.get("press_enter", True)),
            )

        async def handle_screenshot(req: ActionRequest) -> Any:
            return await browser_manager.screenshot()

        capability_registry.register(CAPABILITY_BROWSER_NAVIGATE, handle_navigate)
        capability_registry.register(CAPABILITY_BROWSER_SNAPSHOT, handle_snapshot)
        capability_registry.register(CAPABILITY_BROWSER_CLICK, handle_click)
        capability_registry.register(CAPABILITY_BROWSER_TYPE, handle_type)
        capability_registry.register(CAPABILITY_BROWSER_SCREENSHOT, handle_screenshot)

        self._registered = True
        logger.info("Browser Node capabilities successfully registered.")


# Global singleton instance
browser_node = BrowserNode()
