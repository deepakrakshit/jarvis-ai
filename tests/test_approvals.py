"""Tests for durable approval lifecycle management."""

import sqlite3
from datetime import datetime, timedelta, timezone

from jarvis.contracts.action import ActionRequest, RiskTier
from jarvis.contracts.policy import ApprovalStatus, PolicyVerdict
from jarvis.policy.approvals import ApprovalManager
from jarvis.policy.engine import PolicyEngine
from jarvis.storage.database import DatabaseEngine
from jarvis.storage.migrations import MIGRATIONS


def make_manager(tmp_path) -> ApprovalManager:
    """Create an approval manager backed by an isolated database."""
    return ApprovalManager(database=DatabaseEngine(db_path=tmp_path / "approvals.db"))


def create_request(manager: ApprovalManager):
    """Create a representative high-risk approval request."""
    return manager.create_request(
        action_id="action-1",
        task_id="task-1",
        session_id="session-1",
        capability="shell.execute",
        target_resource="echo safe",
        risk_summary="test approval",
    )


def test_approval_survives_manager_restart(tmp_path) -> None:
    """A pending approval can be loaded by a fresh manager instance."""
    first_manager = make_manager(tmp_path)
    request = create_request(first_manager)

    restarted_manager = make_manager(tmp_path)
    loaded = restarted_manager.get_pending(request.approval_id)

    assert loaded is not None
    assert loaded.status.value == "PENDING"
    assert loaded.session_id == "session-1"
    assert loaded.expires_at is not None

    resolved = restarted_manager.resolve(
        request.approval_id,
        approved=True,
        operator_identity="operator-1",
    )
    assert resolved is not None
    assert resolved.status.value == "APPROVED"

    after_restart = make_manager(tmp_path).get_pending(request.approval_id)
    assert after_restart is not None
    assert after_restart.status.value == "APPROVED"


def test_manager_refreshes_approval_state_from_shared_database(tmp_path) -> None:
    """A manager must observe a resolution made by another manager instance."""
    first_manager = make_manager(tmp_path)
    second_manager = make_manager(tmp_path)
    request = create_request(first_manager)

    assert second_manager.get_pending(request.approval_id) is not None
    assert first_manager.resolve(request.approval_id, approved=True) is not None

    refreshed = second_manager.get_pending(request.approval_id)
    assert refreshed is not None
    assert refreshed.status == ApprovalStatus.APPROVED


def test_approved_ticket_cannot_be_reused_for_another_action(tmp_path) -> None:
    """An approval must remain bound to the action it describes."""
    manager = make_manager(tmp_path)
    request = create_request(manager)
    assert manager.resolve(request.approval_id, approved=True) is not None

    engine = PolicyEngine(
        approvals=manager,
        database=manager.db,
        require_approvals=True,
    )
    decision = engine.evaluate(
        ActionRequest(
            action_id="different-action",
            task_id=request.task_id,
            session_id=request.session_id,
            capability=request.capability,
            arguments={"command": "echo different"},
            risk_tier=RiskTier.HIGH,
            approval_id=request.approval_id,
        )
    )

    assert decision.verdict == PolicyVerdict.DENY
    assert "does not match" in decision.reason


def test_expired_approval_cannot_be_resolved(tmp_path) -> None:
    """Expired approvals transition to TIMED_OUT and cannot authorize work."""
    manager = make_manager(tmp_path)
    request = create_request(manager)
    request.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    manager.db.save_approval_request(request)

    expired = manager.get_pending(request.approval_id)
    assert expired is not None
    assert expired.status.value == "TIMED_OUT"
    assert manager.resolve(request.approval_id, approved=True) is None

    decision = PolicyEngine(
        approvals=manager,
        database=manager.db,
        require_approvals=True,
    ).evaluate(
        ActionRequest(
            action_id=request.action_id,
            task_id=request.task_id,
            session_id=request.session_id,
            capability=request.capability,
            arguments={"command": "echo safe"},
            risk_tier=RiskTier.HIGH,
            approval_id=request.approval_id,
        )
    )
    assert decision.verdict == PolicyVerdict.DENY

    reloaded = make_manager(tmp_path).get_pending(request.approval_id)
    assert reloaded is not None
    assert reloaded.status.value == "TIMED_OUT"


def test_cancelled_approval_cannot_be_reused(tmp_path) -> None:
    """Cancellation revokes an approval and blocks later resolution."""
    manager = make_manager(tmp_path)
    request = create_request(manager)

    cancelled = manager.cancel(request.approval_id, operator_identity="operator-1")
    assert cancelled is not None
    assert cancelled.status.value == "REVOKED"
    assert manager.resolve(request.approval_id, approved=True) is None

    decision = PolicyEngine(
        approvals=manager,
        database=manager.db,
        require_approvals=True,
    ).evaluate(
        ActionRequest(
            action_id=request.action_id,
            task_id=request.task_id,
            session_id=request.session_id,
            capability=request.capability,
            arguments={"command": "echo safe"},
            risk_tier=RiskTier.HIGH,
            approval_id=request.approval_id,
        )
    )
    assert decision.verdict == PolicyVerdict.DENY

    with manager.db.transaction() as cursor:
        audit = cursor.execute(
            """
            SELECT event_type, verdict FROM audit_log
            WHERE action_id = ? AND event_type = 'APPROVAL_REVOKED'
            ORDER BY log_id DESC LIMIT 1
            """,
            (request.action_id,),
        ).fetchone()
    assert audit is not None
    assert audit["event_type"] == "APPROVAL_REVOKED"
    assert audit["verdict"] == ApprovalStatus.REVOKED.value

    reloaded = make_manager(tmp_path).get_pending(request.approval_id)
    assert reloaded is not None
    assert reloaded.status.value == "REVOKED"


def test_legacy_pending_approval_gets_default_expiry_after_migration(tmp_path) -> None:
    """Pending rows from the pre-TTL schema must not remain valid forever."""
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for version, migration in enumerate(MIGRATIONS[:6], start=1):
        connection.executescript(migration)
        connection.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
    connection.execute(
        """
        INSERT INTO approvals (
            approval_id, action_id, task_id, session_id, capability,
            target_resource, risk_summary, arguments_summary_json, status, requested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-approval",
            "legacy-action",
            "legacy-task",
            "legacy-session",
            "shell.execute",
            "echo legacy",
            "legacy approval",
            "{}",
            "PENDING",
            "2020-01-01T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    manager = ApprovalManager(database=DatabaseEngine(db_path=db_path))
    loaded = manager.get_pending("legacy-approval")

    assert loaded is not None
    assert loaded.status == ApprovalStatus.TIMED_OUT
