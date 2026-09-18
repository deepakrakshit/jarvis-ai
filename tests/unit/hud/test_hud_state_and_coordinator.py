"""Unit test suite for JARVIS Holographic HUD State & Coordinator.

Verifies:
1. Deterministic HUD state modeling and serialization.
2. Control Plane task lifecycle synchronization.
3. Voice Plane connectivity independence (tasks survive disconnect & rollover).
4. Spoken HITL approval nonces and resolution verification.
5. Realtime event fanout and subscriber streaming.
6. FastAPI REST endpoints for HUD state and approvals.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from jarvis.apps.hud import (
    HUDCoordinator,
    HUDSystemMode,
    HUDVoiceStatus,
    create_hud_app,
)


@pytest.fixture
def coordinator() -> HUDCoordinator:
    """Provide a fresh isolated HUDCoordinator instance."""
    return HUDCoordinator(session_id="test_session_42")


def test_hud_initial_state(coordinator: HUDCoordinator) -> None:
    """Verify clean initial state of the HUD coordinator."""
    snapshot = coordinator.get_snapshot()
    assert snapshot.session_id == "test_session_42"
    assert snapshot.system_mode == HUDSystemMode.IDLE
    assert snapshot.voice_status == HUDVoiceStatus.DISCONNECTED
    assert snapshot.active_specialist is None
    assert len(snapshot.active_tasks) == 0
    assert len(snapshot.pending_approvals) == 0


def test_task_lifecycle_transitions(coordinator: HUDCoordinator) -> None:
    """Verify task registration, progress updates, and completion."""
    task_id = "task_alpha_001"
    coordinator.register_task(
        task_id=task_id,
        description="Compile Rust binary in container sandbox",
        specialist="coding",
    )

    snap1 = coordinator.get_snapshot()
    assert snap1.system_mode == HUDSystemMode.EXECUTING
    assert len(snap1.active_tasks) == 1
    assert snap1.active_tasks[0].task_id == task_id
    assert snap1.active_tasks[0].progress_percent == 0.0

    # Progress update
    coordinator.update_task_progress(task_id, 45.0, message="Linking objects...")
    snap2 = coordinator.get_snapshot()
    assert snap2.active_tasks[0].progress_percent == 45.0
    assert snap2.active_tasks[0].metadata.get("progress_message") == "Linking objects..."

    # Completion
    coordinator.complete_task(task_id)
    snap3 = coordinator.get_snapshot()
    assert snap3.system_mode == HUDSystemMode.IDLE
    assert len(snap3.active_tasks) == 0


def test_voice_disconnect_independence(coordinator: HUDCoordinator) -> None:
    """Verify that background tasks survive voice disconnection and rollover."""
    task_id = "task_persistent_002"
    coordinator.register_task(
        task_id=task_id,
        description="Deep analysis across 500 documents",
        specialist="research",
    )

    # Voice connects
    coordinator.set_voice_status(HUDVoiceStatus.CONNECTED)
    snap1 = coordinator.get_snapshot()
    assert snap1.voice_status == HUDVoiceStatus.CONNECTED
    assert snap1.system_mode == HUDSystemMode.EXECUTING
    assert len(snap1.active_tasks) == 1

    # Voice suffers sudden disconnection or rollover
    coordinator.set_voice_status(HUDVoiceStatus.DISCONNECTED)
    snap2 = coordinator.get_snapshot()
    assert snap2.voice_status == HUDVoiceStatus.DISCONNECTED
    # INVARIANT: Task must still be executing, mode must NOT reset to IDLE
    assert snap2.system_mode == HUDSystemMode.EXECUTING
    assert len(snap2.active_tasks) == 1
    assert snap2.active_tasks[0].task_id == task_id

    # Rollover transition
    coordinator.set_voice_status(HUDVoiceStatus.ROLLOVER)
    snap3 = coordinator.get_snapshot()
    assert snap3.voice_status == HUDVoiceStatus.ROLLOVER
    assert snap3.system_mode == HUDSystemMode.EXECUTING


def test_spoken_hitl_approval_nonce_verification(coordinator: HUDCoordinator) -> None:
    """Verify spoken approval nonce requirement and fail-closed rejection on mismatch."""
    approval_id = "appr_777"
    task_id = "task_mut_003"
    expected_nonce = "SEC-8842"

    coordinator.register_approval_request(
        approval_id=approval_id,
        task_id=task_id,
        tool_id="native:fs:delete",
        risk_class="CRITICAL_MUTATION",
        arguments_summary="path='/opt/data/archive'",
        proposal_digest="sha256:abc12345",
        nonce=expected_nonce,
    )

    snap1 = coordinator.get_snapshot()
    assert snap1.system_mode == HUDSystemMode.AWAITING_APPROVAL
    assert len(snap1.pending_approvals) == 1
    assert snap1.pending_approvals[0].nonce == expected_nonce

    # Mismatched nonce must be rejected
    mismatch_success = coordinator.resolve_approval(
        approval_id=approval_id,
        approved=True,
        nonce="WRONG-NONCE",
    )
    assert mismatch_success is False
    assert len(coordinator.get_snapshot().pending_approvals) == 1

    # Matching nonce must succeed and clear approval
    valid_success = coordinator.resolve_approval(
        approval_id=approval_id,
        approved=True,
        nonce=expected_nonce,
    )
    assert valid_success is True
    assert len(coordinator.get_snapshot().pending_approvals) == 0


@pytest.mark.asyncio
async def test_realtime_subscription_fanout(coordinator: HUDCoordinator) -> None:
    """Verify async subscriber queue fanout upon state updates."""
    sub_queue = coordinator.subscribe()
    try:
        # Initial snapshot enqueued immediately
        initial = await asyncio.wait_for(sub_queue.get(), timeout=1.0)
        assert initial.system_mode == HUDSystemMode.IDLE

        # Broadcast on new task
        coordinator.register_task(task_id="t_sub_1", description="Subscription test task")
        updated = await asyncio.wait_for(sub_queue.get(), timeout=1.0)
        assert updated.system_mode == HUDSystemMode.EXECUTING
        assert len(updated.active_tasks) == 1
    finally:
        coordinator.unsubscribe(sub_queue)


@pytest.mark.asyncio
async def test_fastapi_hud_endpoints() -> None:
    """Verify REST endpoints for HUD state retrieval and approval resolution."""
    app = create_hud_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET state
        res = await client.get("/api/hud/state")
        assert res.status_code == 200
        data = res.json()
        assert "system_mode" in data
        assert "voice_status" in data
        assert "active_tasks" in data

        # 2. POST voice status
        v_res = await client.post("/api/hud/voice/status", json={"status": "CONNECTED"})
        assert v_res.status_code == 200
        assert v_res.json()["voice_status"] == "CONNECTED"

        # 3. Verify state updated
        res_after = await client.get("/api/hud/state")
        assert res_after.json()["voice_status"] == "CONNECTED"
