"""Tests for ACP External Coding Agent Subsystem.

Verifies Sections 30, 143, and 144 of ARCHITECTURE.md:
- Sandbox boundary confinement and path traversal prevention
- Environment sanitization
- Permission relay policy enforcement
- Structured output validation and ground truth reconciliation
- Full session brokering and event ledger recording
"""

from pathlib import Path
from typing import Dict

import pytest

from jarvis.acp import (
    AcpOutputValidator,
    AcpPermissionRequest,
    AcpPermissionResponse,
    AcpSandboxError,
    AcpSandboxManager,
    AcpSessionSpec,
    AcpSessionState,
    JarvisAgentBroker,
)
from jarvis.policy.engine import PolicyEngine
from jarvis.storage.database import DatabaseEngine


def test_acp_sandbox_path_confinement_and_sanitization(tmp_path: Path) -> None:
    """Verify workspace path confinement and secret stripping in environment."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hello')", encoding="utf-8")

    sandbox = AcpSandboxManager(repo_path=repo)

    # Valid internal paths
    p1 = sandbox.resolve_safe_path("src/main.py")
    assert p1.exists()

    p2 = sandbox.resolve_safe_path("nested/deep/file.txt")
    assert str(p2).startswith(str(repo))

    # Path traversal attack detection
    with pytest.raises(AcpSandboxError):
        sandbox.resolve_safe_path("../../secret.txt")

    with pytest.raises(AcpSandboxError):
        sandbox.resolve_safe_path(tmp_path / "outside.txt")

    # Environment sanitization
    env = sandbox.sanitize_environment(extra_env={"WORKER_PARAM": "123"})
    assert "WORKER_PARAM" in env
    # Master keys must be stripped
    for key in env:
        upper = key.upper()
        assert not upper.startswith("GEMINI_")
        assert not upper.startswith("GROQ_")
        assert not upper.startswith("OPENAI_")


def test_acp_output_validator_structured_schema(tmp_path: Path) -> None:
    """Verify Section 144 output schema parsing and rejection of prose-only output."""
    validator = AcpOutputValidator()

    # 1. Prose-only output must be rejected
    prose_only = "I have completely fixed the bug in the authentication module and everything is tested and working fine!"
    res_prose = validator.validate(prose_only)
    assert res_prose.status == "rejected"
    assert res_prose.tests_passed is False
    assert len(res_prose.remaining_risks) > 0

    # 2. Valid YAML structured output
    valid_yaml = """
I have finished the implementation.

```yaml
result:
  status: completed
  summary: Refactored database schema and verified tests.
  changed_files:
    - src/db.py
    - tests/test_db.py
  tests_run:
    - pytest tests/test_db.py
  tests_passed: true
  artifacts: []
  remaining_risks: []
```
"""
    res_yaml = validator.validate(valid_yaml)
    assert res_yaml.status == "completed"
    assert res_yaml.summary == "Refactored database schema and verified tests."
    assert "src/db.py" in res_yaml.changed_files
    assert res_yaml.tests_passed is True

    # 3. Ground truth reconciliation with sandbox
    repo = tmp_path / "repo_val"
    repo.mkdir()
    sandbox = AcpSandboxManager(repo_path=repo)
    # File created in workspace but not reported by agent
    (repo / "unreported.txt").write_text("extra", encoding="utf-8")

    # Validator should complete without error
    res_with_sandbox = validator.validate(valid_yaml, sandbox=sandbox)
    assert res_with_sandbox.status == "completed"


def test_acp_permission_relay(test_db: DatabaseEngine, tmp_path: Path) -> None:
    """Verify tool allowlist enforcement and approval escalation."""
    repo = tmp_path / "repo_relay"
    repo.mkdir()

    policy = PolicyEngine(database=test_db)
    spec = AcpSessionSpec(
        session_id="acp_test_relay",
        model="GPT-OSS 120B",
        repo_path=repo,
        allowed_tools=["read_file", "write_file"],
        read_only=False,
    )

    approvals_triggered: Dict[str, str] = {}

    def mock_approval_callback(req: AcpPermissionRequest) -> AcpPermissionResponse:
        approvals_triggered[req.command] = "allow-once"
        return AcpPermissionResponse(request_id=req.request_id, decision="allow-once")

    from jarvis.acp.permission_relay import AcpPermissionRelay

    relay = AcpPermissionRelay(
        policy_engine=policy,
        session_spec=spec,
        approval_callback=mock_approval_callback,
    )

    # 1. Allowed tool
    resp1 = relay.evaluate_tool_call("read_file", {"path": "test.txt"})
    assert resp1.decision == "allow-once"

    # 2. Tool not in allowed_tools -> denied
    resp2 = relay.evaluate_tool_call("run_tests", {"command": "pytest"})
    assert resp2.decision == "deny"

    # 3. Read-only constraint
    read_only_spec = AcpSessionSpec(
        session_id="acp_ro",
        model="GPT-OSS 120B",
        repo_path=repo,
        allowed_tools=["read_file", "write_file"],
        read_only=True,
    )
    relay_ro = AcpPermissionRelay(policy_engine=policy, session_spec=read_only_spec)
    resp3 = relay_ro.evaluate_tool_call("write_file", {"path": "test.txt", "content": "x"})
    assert resp3.decision == "deny"


@pytest.mark.asyncio
async def test_acp_broker_full_lifecycle(test_db: DatabaseEngine, tmp_path: Path) -> None:
    """Verify end-to-end broker session creation, tool execution, and ledger."""
    repo = tmp_path / "repo_broker"
    repo.mkdir()
    (repo / "README.md").write_text("# Project Root\n", encoding="utf-8")

    broker = JarvisAgentBroker(database=test_db)

    # 1. Rejection of unapproved model
    with pytest.raises(ValueError, match="not in the approved JARVIS runtime allowlist"):
        broker.create_session(
            AcpSessionSpec(
                model="Unapproved-Model-XYZ",
                repo_path=repo,
            )
        )

    # 2. Creation of valid session
    spec = AcpSessionSpec(
        session_id="acp_sess_live_1",
        model="GPT-OSS 120B",
        repo_path=repo,
        allowed_tools=["read_file", "write_file", "list_dir"],
    )
    created = broker.create_session(spec)
    assert created.session_id == "acp_sess_live_1"

    # Check persistence
    persisted = broker.get_session("acp_sess_live_1")
    assert persisted is not None
    assert persisted["state"] == AcpSessionState.PENDING.value

    # 3. Sandboxed Tool Execution: write_file
    write_res = broker.execute_tool(
        "acp_sess_live_1",
        "write_file",
        {"path": "docs/architecture.md", "content": "# Architecture\nAll systems operational."},
    )
    assert write_res["status"] == "success"

    # Sandboxed Tool Execution: read_file
    read_res = broker.execute_tool(
        "acp_sess_live_1",
        "read_file",
        {"path": "docs/architecture.md"},
    )
    assert read_res["status"] == "success"
    assert "All systems operational" in read_res["content"]

    # Sandboxed Tool Execution: list_dir
    list_res = broker.execute_tool(
        "acp_sess_live_1",
        "list_dir",
        {"path": "."},
    )
    assert list_res["status"] == "success"
    assert "README.md" in list_res["entries"]
    assert "docs" in list_res["entries"]

    # 4. Turn execution with structured candidate output
    candidate = """
Execution finished.

```yaml
result:
  status: completed
  summary: Created docs/architecture.md successfully.
  changed_files:
    - docs/architecture.md
  tests_run: []
  tests_passed: true
  artifacts: []
  remaining_risks: []
```
"""
    result = await broker.execute_turn(
        "acp_sess_live_1",
        instruction="Document the architecture.",
        candidate_output=candidate,
    )
    assert result.status == "completed"
    assert result.summary == "Created docs/architecture.md successfully."
    assert "docs/architecture.md" in result.changed_files

    # Check session state updated to completed
    updated = broker.get_session("acp_sess_live_1")
    assert updated is not None
    assert updated["state"] == AcpSessionState.COMPLETED.value

    # Check event ledger
    ledger = broker.get_ledger("acp_sess_live_1")
    event_types = [evt["event_type"] for evt in ledger]
    assert "session.created" in event_types
    assert "tool.executed" in event_types
    assert "turn.started" in event_types
    assert "turn.completed" in event_types

    # 5. Session cancellation
    broker.cancel_session("acp_sess_live_1")
    cancelled = broker.get_session("acp_sess_live_1")
    assert cancelled is not None
    assert cancelled["state"] == AcpSessionState.CANCELLED.value
