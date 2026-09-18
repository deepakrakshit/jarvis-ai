"""JARVIS HUD Coordinator.

Coordinates deterministic HUD state across the Control Plane, Action Broker,
Voice Plane, and Background Tasks, guaranteeing that UI state reflects true
system reality even during voice disconnects, rollovers, or barge-in events.
"""

import asyncio
import contextlib
import threading
from datetime import UTC, datetime
from typing import Any

from jarvis.apps.hud.schemas import (
    HUDApprovalItem,
    HUDState,
    HUDSystemMode,
    HUDTaskItem,
    HUDTelemetrySummary,
    HUDVoiceStatus,
)
from jarvis.core.logging import get_logger
from jarvis.core.telemetry.metrics import get_metrics

logger = get_logger(__name__)


class HUDCoordinator:
    """Thread-safe and async-safe state coordinator for the Holographic HUD."""

    def __init__(self, session_id: str = "default_hud_session") -> None:
        self._lock = threading.RLock()

        # Authoritative HUD state
        self._state = HUDState(
            session_id=session_id,
            system_mode=HUDSystemMode.IDLE,
            voice_status=HUDVoiceStatus.DISCONNECTED,
            active_specialist=None,
            current_intent=None,
            last_transcript=None,
            last_response=None,
            active_tasks=[],
            pending_approvals=[],
            telemetry=HUDTelemetrySummary(),
            updated_at=datetime.now(UTC),
        )

        # Pending approval resolution callbacks
        self._approval_waiters: dict[str, asyncio.Future[bool]] = {}

        # Subscriber queues for realtime state streaming
        self._subscribers: set[asyncio.Queue[HUDState]] = set()

    @property
    def session_id(self) -> str:
        """Active session identifier tracked by the HUD."""
        return self._state.session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        with self._lock:
            self._state.session_id = value
            self._state.updated_at = datetime.now(UTC)

    def get_snapshot(self) -> HUDState:
        """Return a thread-safe deep snapshot of the authoritative HUD state."""
        with self._lock:
            self._recalculate_telemetry()
            return self._state.model_copy(deep=True)

    def set_voice_status(self, status: HUDVoiceStatus) -> None:
        """Update the voice plane connectivity status without terminating background tasks."""
        with self._lock:
            self._state.voice_status = status
            self._update_system_mode()
            self._state.updated_at = datetime.now(UTC)
        self._broadcast()

    def set_user_intent(self, intent: str | None, specialist: str | None = None) -> None:
        """Update currently recognized user intent and routing specialist."""
        with self._lock:
            self._state.current_intent = intent
            if specialist is not None:
                self._state.active_specialist = specialist
            self._update_system_mode()
            self._state.updated_at = datetime.now(UTC)
        self._broadcast()

    def set_transcript(self, transcript: str) -> None:
        """Update latest recognized speech transcript."""
        with self._lock:
            self._state.last_transcript = transcript
            if self._state.voice_status == HUDVoiceStatus.CONNECTED:
                self._state.system_mode = HUDSystemMode.THINKING
            self._state.updated_at = datetime.now(UTC)
        self._broadcast()

    def set_response(self, response_text: str) -> None:
        """Update latest assistant response."""
        with self._lock:
            self._state.last_response = response_text
            self._update_system_mode()
            self._state.updated_at = datetime.now(UTC)
        self._broadcast()

    # -------------------------------------------------------------------------
    # Task Lifecycle Synchronization (Decoupled from Voice Connection)
    # -------------------------------------------------------------------------

    def register_task(
        self,
        task_id: str,
        description: str,
        specialist: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a new task on the HUD surface."""
        with self._lock:
            # Avoid duplicate task entries
            self._state.active_tasks = [t for t in self._state.active_tasks if t.task_id != task_id]
            new_task = HUDTaskItem(
                task_id=task_id,
                description=description,
                status="RUNNING",
                progress_percent=0.0,
                started_at=datetime.now(UTC),
                specialist=specialist or self._state.active_specialist,
                metadata=metadata or {},
            )
            self._state.active_tasks.append(new_task)
            self._update_system_mode()
            self._state.updated_at = datetime.now(UTC)
        logger.info("hud_task_registered", task_id=task_id, description=description)
        self._broadcast()

    def update_task_progress(
        self, task_id: str, progress_percent: float, message: str | None = None
    ) -> None:
        """Update progress metrics for an ongoing task."""
        with self._lock:
            for t in self._state.active_tasks:
                if t.task_id == task_id:
                    t.progress_percent = max(0.0, min(100.0, progress_percent))
                    if message:
                        t.metadata["progress_message"] = message
                    break
            self._state.updated_at = datetime.now(UTC)
        self._broadcast()

    def complete_task(self, task_id: str) -> None:
        """Mark a task as completed and remove from active list."""
        with self._lock:
            self._state.active_tasks = [t for t in self._state.active_tasks if t.task_id != task_id]
            self._update_system_mode()
            self._state.updated_at = datetime.now(UTC)
        logger.info("hud_task_completed", task_id=task_id)
        self._broadcast()

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed and update error mode."""
        with self._lock:
            self._state.active_tasks = [t for t in self._state.active_tasks if t.task_id != task_id]
            self._update_system_mode()
            self._state.updated_at = datetime.now(UTC)
        logger.warning("hud_task_failed", task_id=task_id, error=error)
        self._broadcast()

    # -------------------------------------------------------------------------
    # Spoken & Interactive Human-in-the-Loop Approvals
    # -------------------------------------------------------------------------

    def register_approval_request(
        self,
        approval_id: str,
        task_id: str,
        tool_id: str,
        risk_class: str,
        arguments_summary: str,
        proposal_digest: str,
        nonce: str,
        expires_at: datetime | None = None,
    ) -> None:
        """Register a pending HITL authorization request with verification nonce."""
        with self._lock:
            item = HUDApprovalItem(
                approval_id=approval_id,
                task_id=task_id,
                tool_id=tool_id,
                risk_class=risk_class,
                arguments_summary=arguments_summary,
                proposal_digest=proposal_digest,
                nonce=nonce,
                created_at=datetime.now(UTC),
                expires_at=expires_at,
            )
            self._state.pending_approvals.append(item)
            self._update_system_mode()
            self._state.updated_at = datetime.now(UTC)
        logger.info(
            "hud_approval_registered", approval_id=approval_id, tool_id=tool_id, nonce=nonce
        )
        self._broadcast()

    def resolve_approval(
        self,
        approval_id: str,
        approved: bool,
        nonce: str | None = None,
    ) -> bool:
        """Resolve a pending approval with strict nonce verification.

        Returns True if the approval was found and successfully resolved, False otherwise.
        """
        with self._lock:
            matched: HUDApprovalItem | None = None
            for item in self._state.pending_approvals:
                if item.approval_id == approval_id:
                    matched = item
                    break

            if not matched:
                logger.warning("hud_approval_not_found", approval_id=approval_id)
                return False

            # Strict verification of spoken nonce if supplied
            if nonce is not None and nonce.strip() != matched.nonce.strip():
                logger.warning(
                    "hud_approval_nonce_mismatch",
                    approval_id=approval_id,
                    expected=matched.nonce,
                    received=nonce,
                )
                return False

            # Remove from pending list
            self._state.pending_approvals = [
                i for i in self._state.pending_approvals if i.approval_id != approval_id
            ]
            self._update_system_mode()
            self._state.updated_at = datetime.now(UTC)

        # Notify any async waiter awaiting this approval
        waiter = self._approval_waiters.pop(approval_id, None)
        if waiter and not waiter.done():
            waiter.set_result(approved)

        logger.info("hud_approval_resolved", approval_id=approval_id, approved=approved)
        self._broadcast()
        return True

    def register_approval_waiter(self, approval_id: str, future: asyncio.Future[bool]) -> None:
        """Register an asyncio future that resolves when the user confirms the action."""
        self._approval_waiters[approval_id] = future

    # -------------------------------------------------------------------------
    # Internal Synchronization & Subscriber Fanout
    # -------------------------------------------------------------------------

    def _update_system_mode(self) -> None:
        """Determine authoritative system mode with fail-closed priority hierarchy."""
        if self._state.pending_approvals:
            self._state.system_mode = HUDSystemMode.AWAITING_APPROVAL
        elif self._state.active_tasks:
            self._state.system_mode = HUDSystemMode.EXECUTING
        elif self._state.voice_status == HUDVoiceStatus.BARGE_IN:
            self._state.system_mode = HUDSystemMode.LISTENING
        elif self._state.voice_status == HUDVoiceStatus.CONNECTED:
            self._state.system_mode = HUDSystemMode.IDLE
        else:
            self._state.system_mode = HUDSystemMode.IDLE

    def _recalculate_telemetry(self) -> None:
        """Populate current telemetry snapshot from global OperationalMetrics."""
        metrics = get_metrics()
        stats = metrics.get_summary()
        self._state.telemetry = HUDTelemetrySummary(
            active_leases_count=len(self._state.active_tasks),
            p95_latency_ms=stats.get("latencies_ms", {}).get("p95", 0.0),
            total_prompt_tokens=stats.get("prompt_tokens", 0),
            total_completion_tokens=stats.get("completion_tokens", 0),
            dlq_dead_letter_count=stats.get("dlq_dead_letters", 0),
        )

    def subscribe(self) -> asyncio.Queue[HUDState]:
        """Subscribe to real-time state broadcasts."""
        q: asyncio.Queue[HUDState] = asyncio.Queue()
        with self._lock:
            self._subscribers.add(q)
            # Immediately enqueue current snapshot
            q.put_nowait(self.get_snapshot())
        return q

    def unsubscribe(self, q: asyncio.Queue[HUDState]) -> None:
        """Unsubscribe from state broadcasts."""
        with self._lock:
            self._subscribers.discard(q)

    def _broadcast(self) -> None:
        """Fan out latest state snapshot to all connected clients."""
        snapshot = self.get_snapshot()
        with self._lock:
            for q in list(self._subscribers):
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(snapshot)


_DEFAULT_HUD_COORDINATOR: HUDCoordinator | None = None


def get_hud_coordinator() -> HUDCoordinator:
    """Retrieve or initialize global HUDCoordinator singleton."""
    global _DEFAULT_HUD_COORDINATOR
    if _DEFAULT_HUD_COORDINATOR is None:
        _DEFAULT_HUD_COORDINATOR = HUDCoordinator()
    return _DEFAULT_HUD_COORDINATOR
