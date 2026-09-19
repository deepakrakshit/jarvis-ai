"""Tests for Policy Engine and Capability Firewall."""

from jarvis.contracts.action import ActionRequest, RiskTier
from jarvis.contracts.policy import ApprovalStatus, PolicyVerdict
from jarvis.policy.approvals import approval_manager
from jarvis.policy.engine import PolicyEngine
from jarvis.policy.firewall import (
    CAPABILITY_FILESYSTEM_DELETE,
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_SHELL_EXECUTE,
    is_dangerous_command,
)


def test_read_only_auto_allow() -> None:
    """Verify read-only filesystem capability is auto-allowed."""
    engine = PolicyEngine()
    req = ActionRequest(
        task_id="TASK-01",
        session_id="SESS-01",
        capability=CAPABILITY_FILESYSTEM_READ,
        arguments={"path": "README.md"},
        risk_tier=RiskTier.READ_ONLY,
    )
    decision = engine.evaluate(req)
    assert decision.verdict == PolicyVerdict.ALLOW
    assert decision.approval_request is None


def test_dangerous_command_blacklisting() -> None:
    """Verify dangerous shell command patterns are rejected immediately."""
    assert is_dangerous_command("rmdir /s /q C:\\") is True
    assert is_dangerous_command("del /f /s /q C:\\Windows") is True
    assert is_dangerous_command("echo hello world") is False

    engine = PolicyEngine()
    req = ActionRequest(
        task_id="TASK-02",
        session_id="SESS-01",
        capability=CAPABILITY_SHELL_EXECUTE,
        arguments={"command": "format C: /fs:ntfs"},
        risk_tier=RiskTier.HIGH,
    )
    decision = engine.evaluate(req)
    assert decision.verdict == PolicyVerdict.DENY
    assert "prohibited destructive pattern" in decision.reason


def test_high_risk_actions_require_approval() -> None:
    """Verify destructive operations trigger an interactive ApprovalRequest."""
    engine = PolicyEngine()
    req = ActionRequest(
        task_id="TASK-03",
        session_id="SESS-01",
        capability=CAPABILITY_FILESYSTEM_DELETE,
        arguments={"path": "C:/some/file.txt"},
        risk_tier=RiskTier.HIGH,
    )
    decision = engine.evaluate(req)
    assert decision.verdict == PolicyVerdict.ASK
    assert decision.approval_request is not None
    assert decision.approval_request.status == ApprovalStatus.PENDING

    # Resolve approval to APPROVED
    appr_id = decision.approval_request.approval_id
    approval_manager.resolve(appr_id, approved=True, operator_identity="user")

    # Re-evaluate with approval attached
    req.approval_id = appr_id
    re_decision = engine.evaluate(req)
    assert re_decision.verdict == PolicyVerdict.ALLOW
    assert "Explicitly approved by operator" in re_decision.reason


def test_unknown_capability_denied() -> None:
    """Verify unknown or unmapped capability fails closed."""
    engine = PolicyEngine()
    req = ActionRequest(
        task_id="TASK-04",
        session_id="SESS-01",
        capability="unrestricted.godmode.hack",
        arguments={},
        risk_tier=RiskTier.CRITICAL,
    )
    decision = engine.evaluate(req)
    assert decision.verdict == PolicyVerdict.DENY
    assert "Unknown or unauthorized capability" in decision.reason
