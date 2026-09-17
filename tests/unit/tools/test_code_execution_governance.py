"""Unit and Governance Tests for Python Code Runner and Recovery System.

Validates zero-trust workspace confinement, shell=False execution,
secret scrubbing, timeout bounding, and LiveToolBridge recovery from blocked shell actions.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from jarvis.core.gateway.realtime import LiveToolCall
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import LiveToolBridge
from jarvis.tools.native.code import run_python_test


@pytest.mark.asyncio
async def test_run_python_test_confinement_breach(tmp_path: Path) -> None:
    """Target file outside approved workspace boundary must be rejected with PermissionError."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()

    evil_script = outside / "leak.py"
    evil_script.write_text("print('hacked')", encoding="utf-8")

    with pytest.raises(PermissionError, match="Confinement breach"):
        await run_python_test(
            file_path=str(evil_script),
            workspace_root=workspace,
        )


@pytest.mark.asyncio
async def test_run_python_test_path_traversal(tmp_path: Path) -> None:
    """Path traversal sequences (..) must be rejected with PermissionError."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(PermissionError, match="Path traversal rejected"):
        await run_python_test(
            file_path="../evil.py",
            workspace_root=workspace,
        )


@pytest.mark.asyncio
async def test_run_python_test_nonexistent_file(tmp_path: Path) -> None:
    """Missing target file returns structured error response without crashing."""
    res = await run_python_test(
        file_path="nonexistent.py",
        workspace_root=tmp_path,
    )
    assert res["status"] == "error"
    assert res["exit_code"] == 1
    assert "does not exist" in res["error"]


@pytest.mark.asyncio
async def test_run_python_test_non_python_file(tmp_path: Path) -> None:
    """Non-Python target file is rejected with ValueError message."""
    text_file = tmp_path / "data.txt"
    text_file.write_text("hello", encoding="utf-8")

    res = await run_python_test(
        file_path="data.txt",
        workspace_root=tmp_path,
    )
    assert res["status"] == "error"
    assert "must be a Python (.py) file" in res["error"]


@pytest.mark.asyncio
async def test_run_python_test_injection_in_args(tmp_path: Path) -> None:
    """Forbidden shell metacharacters in test args must be rejected."""
    script = tmp_path / "dummy.py"
    script.write_text("print('ok')", encoding="utf-8")

    res = await run_python_test(
        file_path="dummy.py",
        test_args=["--flag", "value; rm -rf /"],
        workspace_root=tmp_path,
    )
    assert res["status"] == "error"
    assert "forbidden metacharacters" in res["error"]


@pytest.mark.asyncio
async def test_run_python_test_success(tmp_path: Path) -> None:
    """Valid script execution captures stdout and returns exit_code 0."""
    script = tmp_path / "math_test.py"
    script.write_text(
        "import sys\nprint('RESULT: 42')\nsys.exit(0)\n",
        encoding="utf-8",
    )

    res = await run_python_test(
        file_path="math_test.py",
        workspace_root=tmp_path,
    )
    assert res["status"] == "success"
    assert res["exit_code"] == 0
    assert "RESULT: 42" in res["stdout"]
    assert res["timed_out"] is False


@pytest.mark.asyncio
async def test_run_python_test_failure_traceback(tmp_path: Path) -> None:
    """Failing script returns exit_code 1 with full traceback in stderr."""
    script = tmp_path / "broken.py"
    script.write_text(
        "def compute():\n    return 10 / 0\ncompute()\n",
        encoding="utf-8",
    )

    res = await run_python_test(
        file_path="broken.py",
        workspace_root=tmp_path,
    )
    assert res["status"] == "failed"
    assert res["exit_code"] == 1
    assert "ZeroDivisionError" in res["stderr"]


@pytest.mark.asyncio
async def test_run_python_test_timeout(tmp_path: Path) -> None:
    """Exceeding timeout terminates process and returns timed_out status."""
    script = tmp_path / "sleepy.py"
    script.write_text(
        "import time\ntime.sleep(10)\n",
        encoding="utf-8",
    )

    res = await run_python_test(
        file_path="sleepy.py",
        timeout_seconds=1.0,
        workspace_root=tmp_path,
    )
    assert res["status"] == "timed_out"
    assert res["timed_out"] is True
    assert res["exit_code"] == -1


@pytest.mark.asyncio
async def test_run_python_test_scrubs_secrets(tmp_path: Path) -> None:
    """Environment credentials and secret tokens must be scrubbed from test subprocess."""
    script = tmp_path / "check_env.py"
    script.write_text(
        "import os\n"
        "for k in ['GOOGLE_API_KEY', 'OPENAI_API_KEY', 'JARVIS_VAULT_KEY']:\n"
        "    if os.environ.get(k):\n"
        "        print(f'LEAK: {k}')\n",
        encoding="utf-8",
    )

    with patch.dict(
        os.environ,
        {
            "GOOGLE_API_KEY": "super-secret-key-123",
            "OPENAI_API_KEY": "super-secret-key-456",
            "JARVIS_VAULT_KEY": "vault-secret-789",
        },
    ):
        res = await run_python_test(
            file_path="check_env.py",
            workspace_root=tmp_path,
        )
        assert res["exit_code"] == 0
        assert "LEAK" not in res["stdout"]


@pytest.mark.asyncio
async def test_tool_bridge_routes_run_python_test(tmp_path: Path) -> None:
    """LiveToolBridge routes jarvis_run_python_test through Action Broker when HITL approval is granted."""
    script = tmp_path / "sample_test.py"
    script.write_text("print('ALL_PASSED')\n", encoding="utf-8")

    from jarvis.core.policy.engine import PolicyEngine

    async def approve_all(req: Any) -> bool:
        return True

    task_mgr = BackgroundTaskManager()
    policy_engine = PolicyEngine(workspace_root=tmp_path)
    bridge = LiveToolBridge(
        task_manager=task_mgr,
        policy_engine=policy_engine,
        approval_handler=approve_all,
    )

    tool_call = LiveToolCall(
        call_id="call_test_001",
        name="jarvis_run_python_test",
        arguments={"file_path": "sample_test.py", "workspace_root": str(tmp_path)},
    )

    resp = await bridge.execute_tool_call(
        tool_call=tool_call,
        session_id="00000000-0000-0000-0000-000000000001",
        user_intent="Run the sample test",
    )

    assert resp.response.get("status") == "success"
    assert resp.response.get("exit_code") == 0
    assert "ALL_PASSED" in resp.response.get("stdout", "")


@pytest.mark.asyncio
async def test_tool_bridge_run_python_test_requires_hitl_without_approval(tmp_path: Path) -> None:
    """Without explicit HITL approval, host Python test execution is blocked by policy."""
    script = tmp_path / "unapproved_test.py"
    script.write_text("print('SHOULD_NOT_RUN')\n", encoding="utf-8")

    from jarvis.core.policy.engine import PolicyEngine

    task_mgr = BackgroundTaskManager()
    policy_engine = PolicyEngine(workspace_root=tmp_path)
    # No approval handler provided -> execution blocked
    bridge = LiveToolBridge(task_manager=task_mgr, policy_engine=policy_engine)

    tool_call = LiveToolCall(
        call_id="call_test_unapproved",
        name="jarvis_run_python_test",
        arguments={"file_path": "unapproved_test.py", "workspace_root": str(tmp_path)},
    )

    resp = await bridge.execute_tool_call(
        tool_call=tool_call,
        session_id="00000000-0000-0000-0000-000000000001",
        user_intent="Run the unapproved test",
    )

    assert resp.response.get("requires_approval") is True
    assert "approval" in resp.response.get("error", "").lower()


@pytest.mark.asyncio
async def test_tool_bridge_repeated_blocked_shell_recovery() -> None:
    """LiveToolBridge detects repeated blocked shell calls and returns actionable recovery guidance."""
    task_mgr = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_mgr)

    # First attempt: shell is blocked by HITL requirement without interactive approval
    call1 = LiveToolCall(
        call_id="call_shell_001",
        name="jarvis_shell",
        arguments={"command": "python test_requests.py"},
    )
    resp1 = await bridge.execute_tool_call(
        tool_call=call1,
        session_id="00000000-0000-0000-0000-000000000001",
        user_intent="Run the test script",
    )
    assert resp1.response.get("status") == "blocked"
    assert resp1.response.get("requires_approval") is True
    assert "jarvis_run_python_test" in resp1.response.get("error", "")

    # Second identical attempt: bridge intercepts repeat loop and returns recovery directive
    call2 = LiveToolCall(
        call_id="call_shell_002",
        name="jarvis_shell",
        arguments={"command": "python test_requests.py"},
    )
    resp2 = await bridge.execute_tool_call(
        tool_call=call2,
        session_id="00000000-0000-0000-0000-000000000001",
        user_intent="Run the test script again",
    )
    assert resp2.response.get("status") == "blocked_repeated_attempt"
    assert resp2.response.get("suggested_tool") == "jarvis_run_python_test"
    assert resp2.response.get("suggested_arguments") == {"file_path": "test_requests.py"}
    assert "Do NOT retry jarvis_shell" in resp2.response.get("error", "")


@pytest.mark.asyncio
async def test_tool_bridge_routes_fetch_web() -> None:
    """LiveToolBridge routes jarvis_fetch_web through governed read path."""
    task_mgr = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_mgr)

    with patch("jarvis.tools.native.fetch_url") as mock_fetch:
        mock_fetch.return_value = {
            "url": "https://requests.readthedocs.io/en/latest/",
            "status_code": 200,
            "body": "Requests: HTTP for Humans",
            "text": "Requests: HTTP for Humans",
            "truncated": False,
        }

        call = LiveToolCall(
            call_id="call_fetch_001",
            name="jarvis_fetch_web",
            arguments={"url": "https://requests.readthedocs.io/en/latest/"},
        )
        resp = await bridge.execute_tool_call(
            tool_call=call,
            session_id="00000000-0000-0000-0000-000000000001",
            user_intent="Check the official documentation",
        )
        assert resp.response.get("status") == "success"
        assert resp.response.get("body") == "Requests: HTTP for Humans"


def test_code_run_test_manifest_properties() -> None:
    """native:code:run_test must be classified as NATIVE tool with NON_IDEMPOTENT side effects."""
    from jarvis.core.capabilities.builtin import BUILTIN_CAPABILITIES
    from jarvis.core.capabilities.manifest import RiskClass, SideEffectClass, ToolType

    manifest = next(
        (m for m in BUILTIN_CAPABILITIES if m.capability_id == "native:code:run_test"), None
    )
    assert manifest is not None
    assert manifest.tool_type == ToolType.NATIVE
    assert manifest.side_effect_class == SideEffectClass.NON_IDEMPOTENT
    assert manifest.risk_class == RiskClass.UNBOUNDED_MUTATION
    assert manifest.approval_requirement is True
    assert manifest.sandbox_requirement is True
    assert "HOST PROCESS EXECUTION" in manifest.description
    assert "NOT_ISOLATED" in manifest.description


def test_action_broker_treats_code_execution_as_non_idempotent() -> None:
    """Action Broker must recognize native:code:run_test as NON_IDEMPOTENT and refuse blind retries."""
    from jarvis.core.broker.broker import ActionBroker
    from jarvis.core.broker.retry import RetryClassifier
    from jarvis.core.broker.types import IdempotencyClass, RetryClassification
    from jarvis.core.capabilities.builtin import BUILTIN_CAPABILITIES

    manifest = next(
        (m for m in BUILTIN_CAPABILITIES if m.capability_id == "native:code:run_test"), None
    )
    assert manifest is not None

    resolved_class = ActionBroker.resolve_idempotency_class(manifest)
    assert resolved_class == IdempotencyClass.NON_IDEMPOTENT

    classification = RetryClassifier.classify(
        tool_id="native:code:run_test",
        idempotency_class=resolved_class,
        error=TimeoutError("Subprocess execution timed out"),
        dispatched_to_provider=True,
    )
    assert classification == RetryClassification.AMBIGUOUS_OUTCOME
