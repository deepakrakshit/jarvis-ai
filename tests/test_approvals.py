"""Tests for durable approval lifecycle management."""

from datetime import datetime, timedelta, timezone

from jarvis.policy.approvals import ApprovalManager
from jarvis.storage.database import DatabaseEngine


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

    reloaded = make_manager(tmp_path).get_pending(request.approval_id)
    assert reloaded is not None
    assert reloaded.status.value == "REVOKED"
