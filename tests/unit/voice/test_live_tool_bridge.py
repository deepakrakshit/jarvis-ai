"""Unit tests for LiveToolBridge zero-trust capability execution and gating."""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.gateway.realtime import LiveToolCall
from jarvis.core.policy.decision import PolicyDecision, PolicyDecisionType
from jarvis.core.policy.engine import PolicyEngine
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import CURATED_LIVE_TOOLS, LiveToolBridge


@pytest.mark.asyncio
async def test_tool_bridge_manifest_registration() -> None:
    """Verify all curated tools are registered in the CapabilityRegistry."""
    registry = CapabilityRegistry()
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager, registry=registry)

    # Tool definitions schema check
    tool_defs = bridge.get_tool_definitions()
    assert len(tool_defs) == 1
    assert len(tool_defs[0]["function_declarations"]) == len(CURATED_LIVE_TOOLS)

    for tool_meta in CURATED_LIVE_TOOLS:
        name = tool_meta["name"]
        manifest = registry.get(name)
        assert manifest is not None
        assert manifest.capability_id == name
        assert tool_meta.get("behavior") in ("BLOCKING", "NON_BLOCKING")


@pytest.mark.asyncio
async def test_tool_bridge_get_time_execution() -> None:
    """Verify jarvis_get_time executes deterministically and returns truthful system time."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    call = LiveToolCall(
        call_id="call_time_001",
        name="jarvis_get_time",
        arguments={},
    )
    response = await bridge.execute_tool_call(call, session_id="sess_time_001")
    assert response.name == "jarvis_get_time"
    assert response.response.get("status") == "success"
    assert "local_time" in response.response
    assert "utc_time" in response.response

    # Test explicit timezone query
    call_tz = LiveToolCall(
        call_id="call_time_002",
        name="jarvis_get_time",
        arguments={"timezone": "UTC"},
    )
    response_tz = await bridge.execute_tool_call(call_tz, session_id="sess_time_002")
    assert response_tz.name == "jarvis_get_time"
    assert response_tz.response.get("status") == "success"
    assert response_tz.response.get("timezone") == "UTC"


@pytest.mark.asyncio
async def test_tool_bridge_rejects_unregistered_tools() -> None:
    """Verify untrusted tool call attempting to call unregistered tool is rejected."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    call = LiveToolCall(
        call_id="call_unreg_001",
        name="arbitrary_system_command",
        arguments={"cmd": "whoami"},
    )
    response = await bridge.execute_tool_call(call, session_id="sess_tb_001")

    assert "error" in response.response
    assert "not authorized in JARVIS" in response.response["error"]


@pytest.mark.asyncio
async def test_tool_bridge_policy_engine_denial() -> None:
    """Verify Centralized Policy Engine denial immediately blocks execution."""
    task_manager = BackgroundTaskManager()
    mock_policy_engine = MagicMock(spec=PolicyEngine)
    mock_policy_engine.evaluate_invocation.return_value = PolicyDecision(
        decision=PolicyDecisionType.DENY,
        reason="Action prohibited by security policy",
        risk_score=0.9,
    )

    bridge = LiveToolBridge(
        task_manager=task_manager,
        policy_engine=mock_policy_engine,
    )

    call = LiveToolCall(
        call_id="call_policy_001",
        name="jarvis_task",
        arguments={"objective": "format drive"},
    )
    response = await bridge.execute_tool_call(call, session_id="sess_tb_002")

    assert "error" in response.response
    assert "Security policy blocked action" in response.response["error"]


@pytest.mark.asyncio
async def test_tool_bridge_idempotency_deduplication() -> None:
    """Verify duplicate call_id is caught and suppressed silently."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    call = LiveToolCall(
        call_id="call_idemp_001",
        name="jarvis_chat",
        arguments={"message": "Hello once"},
    )

    first_resp = await bridge.execute_tool_call(call, session_id="sess_tb_003")
    assert first_resp.response.get("status") == "delivered"

    # Second call with identical call_id
    second_resp = await bridge.execute_tool_call(call, session_id="sess_tb_003")
    assert second_resp.response.get("status") == "duplicate"
    assert second_resp.scheduling == "SILENT"


@pytest.mark.asyncio
async def test_tool_bridge_curated_capabilities() -> None:
    """Verify execution and routing across the 7 curated capabilities."""
    task_manager = BackgroundTaskManager()
    executed_work: list[tuple[str, str]] = []

    async def mock_worker(name: str, title: str) -> dict[str, Any]:
        executed_work.append((name, title))
        await asyncio.sleep(0.01)
        return {"status": "ok", "artifact": "result.py"}

    bridge = LiveToolBridge(
        task_manager=task_manager,
        work_executor=mock_worker,
    )

    # 1. jarvis_chat
    chat_call = LiveToolCall(
        call_id="c_chat_1",
        name="jarvis_chat",
        arguments={"message": "Good morning!"},
    )
    chat_resp = await bridge.execute_tool_call(chat_call, session_id="s1")
    assert chat_resp.response["status"] == "delivered"
    assert chat_resp.response["message"] == "Good morning!"

    # 2. jarvis_system_status (empty)
    stat_call = LiveToolCall(call_id="c_stat_1", name="jarvis_system_status", arguments={})
    stat_resp = await bridge.execute_tool_call(stat_call, session_id="s1")
    assert stat_resp.response["health"] == "OPERATIONAL"
    assert stat_resp.response["active_tasks_count"] == 0

    # 3. jarvis_task (dispatches background task)
    task_call = LiveToolCall(
        call_id="c_task_1",
        name="jarvis_task",
        arguments={"objective": "Execute background sync"},
    )
    task_resp = await bridge.execute_tool_call(task_call, session_id="s1")
    assert task_resp.response["status"] == "started"
    bg_tid = task_resp.response["task_id"]

    # 4. jarvis_get_task_status
    query_call = LiveToolCall(
        call_id="c_query_1",
        name="jarvis_get_task_status",
        arguments={"task_id": bg_tid},
    )
    query_resp = await bridge.execute_tool_call(query_call, session_id="s1")
    assert query_resp.response["task_id"] == bg_tid

    # 5. jarvis_read_file (synchronously reads workspace file)
    read_call = LiveToolCall(
        call_id="c_read_1",
        name="jarvis_read_file",
        arguments={"file_path": "pyproject.toml"},
    )
    read_resp = await bridge.execute_tool_call(read_call, session_id="s1")
    assert read_resp.response["status"] == "success"
    assert "pyproject.toml" in read_resp.response["file_name"]
    assert "content" in read_resp.response

    # 6. jarvis_search_web (synchronously searches web)
    search_call = LiveToolCall(
        call_id="c_search_1",
        name="jarvis_search_web",
        arguments={"query": "python asyncio tutorial"},
    )
    search_resp = await bridge.execute_tool_call(search_call, session_id="s1")
    assert search_resp.response["status"] == "success"
    assert "results" in search_resp.response

    # 7. jarvis_code (with target files)
    code_call = LiveToolCall(
        call_id="c_code_1",
        name="jarvis_code",
        arguments={"instruction": "Inspect file", "target_files": ["pyproject.toml"]},
    )
    code_resp = await bridge.execute_tool_call(code_call, session_id="s1")
    assert code_resp.response["status"] == "inspected"
    assert "pyproject.toml" in code_resp.response["files"]

    # 8. jarvis_cancel_task on nonexistent task
    cancel_call = LiveToolCall(
        call_id="c_canc_1",
        name="jarvis_cancel_task",
        arguments={"task_id": "nonexistent_task"},
    )
    cancel_resp = await bridge.execute_tool_call(cancel_call, session_id="s1")
    assert cancel_resp.response["status"] == "not_cancelled"
