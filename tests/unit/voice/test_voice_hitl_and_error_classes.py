"""Unit tests for Voice HITL resume lifecycle, argument binding, and categorical error classes."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from jarvis.core.broker.broker import ActionBroker
from jarvis.core.gateway.realtime import LiveToolCall
from jarvis.core.policy.engine import PolicyEngine
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import LiveToolBridge, LiveToolErrorClass
from jarvis.core.voice.voice_agent import LiveVoiceAgent


@pytest.mark.asyncio
async def test_hitl_approval_resumes_exact_pending_action(tmp_path: Any) -> None:
    """Verify that a tool blocked on REQUIRE_HITL resumes execution immediately once approved."""
    task_manager = BackgroundTaskManager()
    policy_engine = PolicyEngine(workspace_root=tmp_path)
    bridge = LiveToolBridge(task_manager=task_manager, policy_engine=policy_engine)

    # Create a test script in the workspace
    test_script = tmp_path / "sample_test.py"
    test_script.write_text("print('EXEC_SUCCESS')\n", encoding="utf-8")

    call = LiveToolCall(
        call_id="call_test_001",
        name="jarvis_run_python_test",
        arguments={"file_path": str(test_script), "mode": "script"},
    )

    # 1. First execution attempt blocks pending approval
    resp_1 = await bridge.execute_tool_call(call, session_id="sess_001")
    assert resp_1.response.get("status") == "blocked"
    assert resp_1.response.get("error_class") == LiveToolErrorClass.APPROVAL_REQUIRED.value
    assert resp_1.response.get("requires_approval") is True
    assert "request_id" in resp_1.response

    # 2. Human grants approval
    auth = bridge.resolve_pending_approval(approved=True, session_id=uuid4())
    assert auth is not None
    assert auth.is_valid() is True

    # 3. Model retries tool call in next turn with identical arguments
    call_retry = LiveToolCall(
        call_id="call_test_002",
        name="jarvis_run_python_test",
        arguments={"file_path": str(test_script), "mode": "script"},
    )
    resp_2 = await bridge.execute_tool_call(call_retry, session_id="sess_001")
    assert resp_2.response.get("status") == "success"
    assert resp_2.response.get("exit_code") == 0
    assert "EXEC_SUCCESS" in resp_2.response.get("stdout", "")


@pytest.mark.asyncio
async def test_wrong_approval_or_argument_mismatch_fails_closed(tmp_path: Any) -> None:
    """Verify that an authorization for action A cannot authorize modified action B."""
    task_manager = BackgroundTaskManager()
    policy_engine = PolicyEngine(workspace_root=tmp_path)
    bridge = LiveToolBridge(task_manager=task_manager, policy_engine=policy_engine)

    # 1. Block first action
    call_a = LiveToolCall(
        call_id="call_a",
        name="jarvis_run_python_test",
        arguments={"file_path": "script_a.py"},
    )
    await bridge.execute_tool_call(call_a, session_id="sess_002")

    # 2. Grant approval for action A
    auth = bridge.resolve_pending_approval(approved=True)
    assert auth is not None

    # 3. Attempt action B with different file path
    call_b = LiveToolCall(
        call_id="call_b",
        name="jarvis_run_python_test",
        arguments={"file_path": "malicious_script.py"},
    )
    resp_b = await bridge.execute_tool_call(call_b, session_id="sess_002")

    # Action B must be blocked because arguments hash does not match
    assert resp_b.response.get("status") == "blocked"
    assert resp_b.response.get("error_class") == LiveToolErrorClass.APPROVAL_REQUIRED.value


@pytest.mark.asyncio
async def test_stale_or_expired_approval_fails_closed(tmp_path: Any) -> None:
    """Verify that an expired authorization cannot be used to resume execution."""
    task_manager = BackgroundTaskManager()
    policy_engine = PolicyEngine(workspace_root=tmp_path)
    bridge = LiveToolBridge(task_manager=task_manager, policy_engine=policy_engine)

    call = LiveToolCall(
        call_id="call_exp_1",
        name="jarvis_run_python_test",
        arguments={"file_path": "exp.py"},
    )
    await bridge.execute_tool_call(call, session_id="sess_003")

    auth = bridge.resolve_pending_approval(approved=True)
    assert auth is not None

    # Simulate TTL expiration
    auth.expires_at = datetime.now(UTC) - timedelta(seconds=60)
    assert auth.is_valid() is False

    # Retry must block
    call_retry = LiveToolCall(
        call_id="call_exp_2",
        name="jarvis_run_python_test",
        arguments={"file_path": "exp.py"},
    )
    resp = await bridge.execute_tool_call(call_retry, session_id="sess_003")
    assert resp.response.get("status") in ("blocked", "blocked_repeated_attempt")
    assert resp.response.get("error_class") == LiveToolErrorClass.APPROVAL_REQUIRED.value


@pytest.mark.asyncio
async def test_consumed_authorization_cannot_be_replayed(tmp_path: Any) -> None:
    """Verify that single-use EffectAuthorization cannot be replayed after commit."""
    task_manager = BackgroundTaskManager()
    policy_engine = PolicyEngine(workspace_root=tmp_path)
    bridge = LiveToolBridge(task_manager=task_manager, policy_engine=policy_engine)

    test_file = tmp_path / "replay_test.py"
    test_file.write_text("print('ONCE')\n", encoding="utf-8")

    # 1. Block and approve
    call_1 = LiveToolCall(
        call_id="call_r1",
        name="jarvis_run_python_test",
        arguments={"file_path": str(test_file)},
    )
    await bridge.execute_tool_call(call_1, session_id="sess_004")
    bridge.resolve_pending_approval(approved=True)

    # 2. Execute successfully (consumes token)
    call_2 = LiveToolCall(
        call_id="call_r2",
        name="jarvis_run_python_test",
        arguments={"file_path": str(test_file)},
    )
    resp_2 = await bridge.execute_tool_call(call_2, session_id="sess_004")
    assert resp_2.response.get("status") == "success"

    # 3. Third attempt with identical arguments must block because token was consumed
    call_3 = LiveToolCall(
        call_id="call_r3",
        name="jarvis_run_python_test",
        arguments={"file_path": str(test_file)},
    )
    resp_3 = await bridge.execute_tool_call(call_3, session_id="sess_004")
    assert resp_3.response.get("status") in ("blocked", "blocked_repeated_attempt")
    assert resp_3.response.get("error_class") == LiveToolErrorClass.APPROVAL_REQUIRED.value


@pytest.mark.asyncio
async def test_unsolicited_mutation_on_readonly_query() -> None:
    """Verify read-only query (checking Python version) blocks unsolicited helper script creation."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    call = LiveToolCall(
        call_id="call_ver_001",
        name="jarvis_write_file",
        arguments={"file_path": "check_version.py", "content": "import sys; print(sys.version)"},
    )

    # User asked for python version
    resp = await bridge.execute_tool_call(
        call,
        session_id="sess_005",
        user_intent="check python version",
    )

    assert resp.response.get("status") == "blocked"
    assert resp.response.get("error_class") == LiveToolErrorClass.UNSOLICITED_MUTATION.value
    assert "Security policy blocked action" in resp.response.get("error", "")


@pytest.mark.asyncio
async def test_categorical_error_classes() -> None:
    """Verify categorical error classifications for UNAVAILABLE, NOT_FOUND, OUTSIDE_SCOPE."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    # 1. UNAVAILABLE for unregistered tool
    call_unreg = LiveToolCall(
        call_id="c_unreg",
        name="unknown_external_tool",
        arguments={},
    )
    resp_unreg = await bridge.execute_tool_call(call_unreg, session_id="s1")
    assert resp_unreg.response.get("error_class") == LiveToolErrorClass.UNAVAILABLE.value

    # 2. NOT_FOUND for non-existent file
    call_not_found = LiveToolCall(
        call_id="c_nf",
        name="jarvis_read_file",
        arguments={"file_path": "non_existent_file_xyz_123.txt"},
    )
    resp_nf = await bridge.execute_tool_call(call_not_found, session_id="s1")
    assert resp_nf.response.get("error_class") == LiveToolErrorClass.NOT_FOUND.value

    # 3. OUTSIDE_SCOPE for path traversal
    call_traversal = LiveToolCall(
        call_id="c_trav",
        name="jarvis_read_file",
        arguments={"file_path": "../../etc/shadow"},
    )
    resp_trav = await bridge.execute_tool_call(call_traversal, session_id="s1")
    assert resp_trav.response.get("error_class") == LiveToolErrorClass.OUTSIDE_SCOPE.value


@pytest.mark.asyncio
async def test_voice_agent_user_text_approval_resumes_execution(tmp_path: Any) -> None:
    """Verify voice agent resolves pending approval when user inputs approval text."""
    task_manager = BackgroundTaskManager()
    policy_engine = PolicyEngine(workspace_root=tmp_path)
    bridge = LiveToolBridge(task_manager=task_manager, policy_engine=policy_engine)

    mock_adapter = AsyncMock()

    agent = LiveVoiceAgent(
        session_id="voice_sess_test",
        adapter=mock_adapter,
        task_manager=task_manager,
        tool_bridge=bridge,
    )

    test_file = tmp_path / "voice_exec.py"
    test_file.write_text("print('VOICE_EXEC_SUCCESS')\n", encoding="utf-8")

    # 1. Model attempts execution, blocks pending HITL
    call = LiveToolCall(
        call_id="call_v1",
        name="jarvis_run_python_test",
        arguments={"file_path": str(test_file)},
    )
    resp_1 = await bridge.execute_tool_call(call, session_id="voice_sess_test")
    assert resp_1.response.get("status") == "blocked"

    # 2. User sends "APPROVED" via voice text
    await agent.send_user_text("APPROVED")

    # 3. Verify active authorization was minted
    args_hash = ActionBroker.compute_canonical_hash({"file_path": str(test_file)})
    auth = policy_engine.hitl_pipeline.find_active_authorization(
        tool_id="jarvis_run_python_test",
        canonical_arguments_hash=args_hash,
    )
    assert auth is not None
    assert auth.is_valid() is True

    # 4. Model retries tool call in response to approval
    call_retry = LiveToolCall(
        call_id="call_v2",
        name="jarvis_run_python_test",
        arguments={"file_path": str(test_file)},
    )
    resp_2 = await bridge.execute_tool_call(call_retry, session_id="voice_sess_test")
    assert resp_2.response.get("status") == "success"
    assert "VOICE_EXEC_SUCCESS" in resp_2.response.get("stdout", "")
