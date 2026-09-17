"""Unit tests for Canonical Capability Projection and Action Broker convergence in the Voice Plane.

Verifies:
1. Dynamic capability projection via CapabilityFirewall and least-privilege scoping
2. Exit gate: given different task scopes, visible tool lists differ as policy predicts
3. Autonomy Level 0 (Observe Only) projection filtering
4. Mutating tool routing strictly through ActionBroker with commit-time EffectAuthorization
5. Strict enforcement of REQUIRE_HITL without fall-through
6. Governed fast read path execution (file listing, math calculator, system clock)
7. Workspace boundary confinement and path traversal defense
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from jarvis.core.broker.broker import ActionBroker
from jarvis.core.gateway.realtime import LiveToolCall
from jarvis.core.policy.decision import (
    EffectAuthorization,
    PolicyDecision,
    PolicyDecisionType,
)
from jarvis.core.policy.engine import PolicyEngine
from jarvis.core.policy.hitl import ApprovalRequest
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import LiveToolBridge


@pytest.mark.asyncio
async def test_dynamic_tool_projection_scoping() -> None:
    """Verify visible tool lists differ between different task scopes as policy predicts."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    # Task A: Read-only scope
    scopes_read_only = {"filesystem:read", "system:clock"}
    tool_defs_a = bridge.get_tool_definitions(task_scopes=scopes_read_only)
    decl_names_a = [d["name"] for d in tool_defs_a[0]["function_declarations"]]

    assert "native_fs_read_file" in decl_names_a
    assert "native_clock_get_time" in decl_names_a
    # Mutating tools must be hidden under read-only scope
    assert "native_fs_write_file" not in decl_names_a
    assert "native_shell_execute" not in decl_names_a

    # Task B: Expanded scope including write
    scopes_read_write = {"filesystem:read", "filesystem:write", "system:clock"}
    tool_defs_b = bridge.get_tool_definitions(task_scopes=scopes_read_write)
    decl_names_b = [d["name"] for d in tool_defs_b[0]["function_declarations"]]

    assert "native_fs_read_file" in decl_names_b
    assert "native_clock_get_time" in decl_names_b
    assert "native_fs_write_file" in decl_names_b
    assert "native_shell_execute" not in decl_names_b


@pytest.mark.asyncio
async def test_dynamic_tool_projection_autonomy_level_0() -> None:
    """Verify Autonomy Level 0 (Observe Only) hides all mutating capabilities."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    all_scopes = {
        "filesystem:read",
        "filesystem:write",
        "network:fetch",
        "math:evaluate",
        "system:clock",
    }
    tool_defs = bridge.get_tool_definitions(task_scopes=all_scopes, autonomy_level=0)
    decl_names = [d["name"] for d in tool_defs[0]["function_declarations"]]

    # Read tools remain visible
    assert "native_fs_read_file" in decl_names
    assert "native_clock_get_time" in decl_names
    # Mutating tools are strictly filtered out by Autonomy Level 0
    assert "native_fs_write_file" not in decl_names


@pytest.mark.asyncio
async def test_voice_action_broker_convergence_mutating_tool(tmp_path: Path) -> None:
    """Verify mutating Live tool call routes strictly through ActionBroker with EffectAuthorization."""
    task_manager = BackgroundTaskManager()
    mock_broker = MagicMock(spec=ActionBroker)
    mock_broker.execute_action = AsyncMock(return_value={"status": "success", "bytes_written": 18})

    bridge = LiveToolBridge(
        task_manager=task_manager,
        broker=mock_broker,
    )

    test_file = "scratch/voice_write_test.txt"
    call = LiveToolCall(
        call_id="call_write_001",
        name="jarvis_write_file",
        arguments={"file_path": test_file, "content": "Autonomous content"},
    )

    resp = await bridge.execute_tool_call(call, session_id="sess_write_001")

    assert resp.name == "jarvis_write_file"
    assert resp.response.get("status") == "success"

    # Verify mock_broker.execute_action was invoked with valid EffectAuthorization
    assert mock_broker.execute_action.await_count == 1
    call_kwargs = mock_broker.execute_action.await_args.kwargs

    auth = call_kwargs.get("authorization")
    assert auth is not None
    assert isinstance(auth, EffectAuthorization)
    assert auth.tool_id == "jarvis_write_file"
    assert auth.target_resource == test_file
    assert auth.canonical_arguments_hash == ActionBroker.compute_canonical_hash(call.arguments)


@pytest.mark.asyncio
async def test_voice_require_hitl_blocks_execution_without_approval() -> None:
    """Verify REQUIRE_HITL decision strictly halts execution and returns a blocked status."""
    task_manager = BackgroundTaskManager()
    mock_policy = MagicMock(spec=PolicyEngine)
    mock_policy.hitl_pipeline = PolicyEngine().hitl_pipeline
    mock_policy.evaluate_invocation.return_value = PolicyDecision(
        decision=PolicyDecisionType.REQUIRE_HITL,
        reason="Dangerous shell command mandates explicit human operator confirmation.",
        risk_score=0.95,
        obligations=["hitl", "sandbox"],
    )

    mock_broker = MagicMock(spec=ActionBroker)
    mock_broker.execute_action = AsyncMock()

    bridge = LiveToolBridge(
        task_manager=task_manager,
        policy_engine=mock_policy,
        broker=mock_broker,
    )

    call = LiveToolCall(
        call_id="call_hitl_001",
        name="jarvis_shell",
        arguments={"command": "rm -rf /"},
    )

    resp = await bridge.execute_tool_call(call, session_id="sess_hitl_001")

    assert resp.response.get("status") == "blocked"
    assert resp.response.get("requires_approval") is True
    assert "Human-in-the-loop approval required" in resp.response.get("error", "")
    assert "request_id" in resp.response

    # ActionBroker must NEVER have been called
    mock_broker.execute_action.assert_not_called()


@pytest.mark.asyncio
async def test_voice_require_hitl_proceeds_when_approved() -> None:
    """Verify REQUIRE_HITL proceeds through ActionBroker when operator approval is granted."""
    task_manager = BackgroundTaskManager()
    real_policy = PolicyEngine()

    mock_broker = MagicMock(spec=ActionBroker)
    mock_broker.execute_action = AsyncMock(
        return_value={"stdout": "hello sandbox\n", "exit_code": 0}
    )

    async def auto_approver(req: ApprovalRequest) -> bool:
        assert req.tool_id == "jarvis_shell"
        return True

    bridge = LiveToolBridge(
        task_manager=task_manager,
        policy_engine=real_policy,
        broker=mock_broker,
        approval_handler=auto_approver,
    )

    call = LiveToolCall(
        call_id="call_hitl_002",
        name="jarvis_shell",
        arguments={"command": "echo hello sandbox"},
    )

    resp = await bridge.execute_tool_call(call, session_id=str(uuid4()))

    assert resp.response.get("status") == "success"
    assert mock_broker.execute_action.await_count == 1

    # Verify authorization token was resolved and passed
    auth = mock_broker.execute_action.await_args.kwargs.get("authorization")
    assert auth is not None
    assert isinstance(auth, EffectAuthorization)
    assert auth.tool_id == "jarvis_shell"


@pytest.mark.asyncio
async def test_voice_governed_read_tools() -> None:
    """Verify safe read path tools (listing directory, calculating math) execute deterministically."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    # 1. Directory Listing
    list_call = LiveToolCall(
        call_id="call_list_001",
        name="jarvis_list_dir",
        arguments={"dir_path": "jarvis"},
    )
    list_resp = await bridge.execute_tool_call(list_call, session_id="sess_read_001")
    assert list_resp.response.get("status") == "success"
    assert "entries" in list_resp.response
    assert any(e["name"] == "core" for e in list_resp.response["entries"])

    # 2. Math Expression Calculator
    calc_call = LiveToolCall(
        call_id="call_calc_001",
        name="jarvis_calc",
        arguments={"expression": "2 ** 8 + 44"},
    )
    calc_resp = await bridge.execute_tool_call(calc_call, session_id="sess_read_002")
    assert calc_resp.response.get("status") == "success"
    assert calc_resp.response.get("result") == 300


@pytest.mark.asyncio
async def test_voice_path_traversal_defense() -> None:
    """Verify attempts to escape the workspace boundary are rejected with permission errors."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    # Traversal attempt on file read
    call = LiveToolCall(
        call_id="call_trav_001",
        name="jarvis_read_file",
        arguments={"file_path": "../../etc/shadow"},
    )
    resp = await bridge.execute_tool_call(call, session_id="sess_trav_001")
    assert "error" in resp.response
    assert (
        "outside workspace root" in resp.response["error"] or "forbidden" in resp.response["error"]
    )
