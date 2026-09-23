"""Live End-to-End Dialogue & System Interaction Tests for JARVIS."""

import pytest

from jarvis.config import settings
from jarvis.contracts.task import TaskState, TaskType
from jarvis.core.control_plane import ControlPlane
from jarvis.execution.browser.host import browser_node
from jarvis.execution.browser.session import browser_manager
from jarvis.execution.windows.host import windows_node


@pytest.fixture(autouse=True)
def setup_nodes() -> None:
    """Ensure host and browser execution nodes are registered."""
    windows_node.register_capabilities()
    browser_node.register_capabilities()


@pytest.mark.asyncio
async def test_live_conversation_host_and_model() -> None:
    """Verify live conversational task execution across host metrics and cognitive models."""
    if not settings.GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY not configured")

    cp = ControlPlane()

    # Turn 1: Host system metrics inspection
    t1 = await cp.submit_intent(
        raw_intent="Check system status and report host metrics.",
        session_id="LIVE-CONV-TEST-01",
    )
    assert t1.state == TaskState.COMPLETED
    assert t1.task_type == TaskType.WINDOWS_CONTROL
    assert t1.verification_passed is True

    # Turn 2: Cognitive reasoning via model router
    t2 = await cp.submit_intent(
        raw_intent="In one short sentence, state your operational readiness as JARVIS.",
        session_id="LIVE-CONV-TEST-01",
    )
    if (
        t2.state == TaskState.FAILED
        and t2.error_message
        and (
            "429" in t2.error_message
            or "RESOURCE_EXHAUSTED" in t2.error_message
            or "rate limit" in t2.error_message.lower()
        )
    ):
        pytest.skip(f"Live model rate limit temporarily reached: {t2.error_message}")

    assert t2.state == TaskState.COMPLETED
    assert t2.result_summary is not None
    assert len(t2.result_summary) > 0


@pytest.mark.asyncio
async def test_live_conversation_autonomous_browser() -> None:
    """Verify autonomous browser navigation and structured DOM extraction."""
    cp = ControlPlane()

    try:
        t = await cp.submit_intent(
            raw_intent="browse https://httpbin.org/get and inspect the page",
            session_id="LIVE-CONV-TEST-02",
        )
        assert t.state == TaskState.COMPLETED
        assert t.task_type == TaskType.BROWSER_AUTOMATION
        assert t.verification_passed is True
    finally:
        await browser_manager.close()
