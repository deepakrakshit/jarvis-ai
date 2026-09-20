"""Tests for Browser Node Execution Capabilities via Playwright."""

import pytest

from jarvis.actions.broker import ActionBroker
from jarvis.contracts.action import ActionRequest, ActionStatus, ExecutionTarget
from jarvis.execution.browser.host import browser_node
from jarvis.execution.browser.session import browser_manager
from jarvis.policy.firewall import (
    CAPABILITY_BROWSER_NAVIGATE,
    CAPABILITY_BROWSER_SCREENSHOT,
    CAPABILITY_BROWSER_SNAPSHOT,
)


@pytest.fixture(autouse=True)
def setup_browser() -> None:
    """Ensure browser capabilities are registered."""
    browser_node.register_capabilities()


@pytest.mark.asyncio
async def test_browser_navigation_and_snapshot() -> None:
    """Verify navigating to HTML and extracting structured DOM snapshot."""
    test_html_url = (
        "data:text/html,<html><head><title>JARVIS Test Page</title></head>"
        "<body><h1>JARVIS Browser Online</h1><p>Automated browsing verified.</p></body></html>"
    )

    try:
        nav_res = await browser_manager.navigate(test_html_url)
        assert nav_res["action"] == "navigate"
        assert nav_res["title"] == "JARVIS Test Page"

        snap_res = await browser_manager.snapshot()
        assert snap_res["action"] == "snapshot"
        assert snap_res["title"] == "JARVIS Test Page"
        assert "JARVIS Browser Online" in snap_res["text"]
        assert "Automated browsing verified." in snap_res["text"]

    finally:
        await browser_manager.close()


@pytest.mark.asyncio
async def test_browser_action_broker_pipeline() -> None:
    """Verify ActionBroker executing browser actions end-to-end."""
    broker = ActionBroker()
    test_html_url = (
        "data:text/html,<html><head><title>Broker Verification</title></head>"
        "<body><h2>Broker Pipeline Active</h2></body></html>"
    )

    try:
        # Navigate
        nav_req = ActionRequest(
            task_id="TASK-BROWSER-1",
            session_id="SESS-01",
            capability=CAPABILITY_BROWSER_NAVIGATE,
            arguments={"url": test_html_url},
            target=ExecutionTarget.BROWSER_NODE,
        )
        res1 = await broker.execute(nav_req)
        assert res1.status == ActionStatus.SUCCEEDED
        assert res1.verified is True
        assert res1.output["title"] == "Broker Verification"

        # Snapshot
        snap_req = ActionRequest(
            task_id="TASK-BROWSER-1",
            session_id="SESS-01",
            capability=CAPABILITY_BROWSER_SNAPSHOT,
            arguments={},
            target=ExecutionTarget.BROWSER_NODE,
        )
        res2 = await broker.execute(snap_req)
        assert res2.status == ActionStatus.SUCCEEDED
        assert "Broker Pipeline Active" in res2.output["text"]

        # Screenshot
        shot_req = ActionRequest(
            task_id="TASK-BROWSER-1",
            session_id="SESS-01",
            capability=CAPABILITY_BROWSER_SCREENSHOT,
            arguments={},
            target=ExecutionTarget.BROWSER_NODE,
        )
        res3 = await broker.execute(shot_req)
        assert res3.status == ActionStatus.SUCCEEDED
        assert "artifact_path" in res3.output

    finally:
        await browser_manager.close()


@pytest.mark.asyncio
async def test_browser_typing_clicking_and_elements() -> None:
    """Verify typing, enter submission, dynamic selector fallback, and element extraction."""
    test_html_url = (
        "data:text/html,<html><head><title>Form Interactive Test</title></head>"
        "<body>"
        "<form id='search-form' onsubmit='event.preventDefault(); document.getElementById(\"result\").innerText = document.getElementById(\"query-box\").value;'>"
        "<input id='query-box' name='search_query' type='text' />"
        "<button id='submit-btn' type='submit'>Search</button>"
        "</form>"
        "<div id='result'>initial</div>"
        "<a id='video-title' href='/video-link'>Sample Video Title</a>"
        "</body></html>"
    )

    try:
        await browser_manager.navigate(test_html_url)

        # Type with press_enter=True into search field
        type_res = await browser_manager.type_text(
            selector="input[name=search_query]",
            text="Clash of Clans TH19",
            press_enter=True,
        )
        assert type_res["action"] == "type"
        assert type_res["text"] == "Clash of Clans TH19"

        # Check snapshot to confirm dynamic element extraction
        snap = await browser_manager.snapshot()
        assert "Key Interactive Elements" in snap["text"]
        assert "input[name=search_query]" in snap["text"]

        # Click fallback testing (click on link using fallback selector logic)
        click_res = await browser_manager.click("a#video-title")
        assert click_res["action"] == "click"

    finally:
        await browser_manager.close()
