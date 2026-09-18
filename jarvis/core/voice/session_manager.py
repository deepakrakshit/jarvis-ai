"""JARVIS Live Session Manager.

Maintains the strict architectural separation across the three core lifecycles:
1. JARVIS Session (logical user conversation state)
2. Provider Connection (ephemeral WebSocket connection with GoAway rotations)
3. Background Tasks (long-running autonomous workloads)

Coordinates transparent reconnection, session resumption tokens, and context compression.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.core.config import get_settings
from jarvis.core.exceptions import ModelProviderError
from jarvis.core.gateway.realtime import (
    LiveGoAway,
    LiveResumptionUpdate,
    LiveSessionConfig,
    RealtimeModelAdapter,
)
from jarvis.core.logging import get_logger
from jarvis.core.voice.telemetry import VoiceTelemetryLogger

logger = get_logger(__name__)


class ConnectionRecord(BaseModel):
    """Metadata tracking a single ephemeral WebSocket provider connection."""

    connection_id: str
    connected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    disconnected_at: datetime | None = None
    goaway_received_at: datetime | None = None
    resumed_from_handle: str | None = None
    emitted_new_handle: str | None = None


class LiveVoiceSession(BaseModel):
    """Canonical representation of an ongoing realtime voice conversation."""

    jarvis_session_id: str
    active_connection_id: str
    model_id: str = Field(default_factory=lambda: get_settings().REALTIME_VOICE_MODEL_ID)
    voice_name: str = Field(default_factory=lambda: get_settings().VOICE_DEFAULT_NAME)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latest_resumption_handle: str | None = None
    is_resumable: bool = True
    connection_history: list[ConnectionRecord] = Field(default_factory=list)
    attached_task_ids: list[str] = Field(default_factory=list)
    is_active: bool = True


class LiveSessionManager:
    """Oversees connection stability, transparent resumption, and GoAway rotation."""

    def __init__(
        self,
        adapter: RealtimeModelAdapter,
        telemetry: VoiceTelemetryLogger | None = None,
    ) -> None:
        self.adapter = adapter
        self.telemetry = telemetry
        self._active_session: LiveVoiceSession | None = None
        self._session_config: LiveSessionConfig | None = None
        self._reconnecting = False
        self._lock = asyncio.Lock()

    @property
    def current_session(self) -> LiveVoiceSession | None:
        """Return the active voice session record."""
        return self._active_session

    async def initialize_session(
        self,
        jarvis_session_id: str,
        config: LiveSessionConfig,
    ) -> LiveVoiceSession:
        """Create and establish a new logical voice session with provider connection."""
        async with self._lock:
            conn_id = f"conn_{uuid4().hex[:8]}"
            self._session_config = config

            if self.telemetry:
                self.telemetry.log_session_created(config.model_id)

            # Establish initial connection
            await self.adapter.connect(config)

            conn_rec = ConnectionRecord(
                connection_id=conn_id,
                resumed_from_handle=config.resumption_handle,
            )

            session = LiveVoiceSession(
                jarvis_session_id=jarvis_session_id,
                active_connection_id=conn_id,
                model_id=config.model_id,
                voice_name=config.voice_name,
                latest_resumption_handle=config.resumption_handle,
                connection_history=[conn_rec],
            )
            self._active_session = session

            if self.telemetry:
                self.telemetry.log_session_connected(conn_id, config.model_id)

            return session

    def update_resumption_handle(self, update: LiveResumptionUpdate) -> None:
        """Update tracked resumption handle emitted by the provider."""
        if self._active_session:
            self._active_session.latest_resumption_handle = update.handle
            self._active_session.is_resumable = update.resumable
            self._active_session.updated_at = datetime.now(UTC)

            if self._active_session.connection_history:
                self._active_session.connection_history[-1].emitted_new_handle = update.handle

            if self.telemetry and update.resumable:
                self.telemetry.log_session_resumed(update.handle)

    async def handle_go_away(self, go_away: LiveGoAway) -> None:
        """Handle server GoAway warning by transparently replacing the connection."""
        if not self._active_session or not self._session_config or self._reconnecting:
            return

        async with self._lock:
            self._reconnecting = True
            logger.warning(
                "initiating_transparent_connection_rotation",
                session_id=self._active_session.jarvis_session_id,
                time_left_seconds=go_away.time_left_seconds,
            )

            if self.telemetry:
                self.telemetry.log_goaway_received(go_away.time_left_seconds)

            old_conn_id = self._active_session.active_connection_id
            if self._active_session.connection_history:
                self._active_session.connection_history[-1].goaway_received_at = datetime.now(UTC)
                self._active_session.connection_history[-1].disconnected_at = datetime.now(UTC)

            # 1. Close current connection
            await self.adapter.close()

            # 2. Build resumption config with latest handle
            new_conn_id = f"conn_{uuid4().hex[:8]}"
            resume_handle = self._active_session.latest_resumption_handle

            resume_config = self._session_config.model_copy(
                update={"resumption_handle": resume_handle}
            )

            # 3. Connect replacement connection
            try:
                await self.adapter.connect(resume_config)

                new_conn_rec = ConnectionRecord(
                    connection_id=new_conn_id,
                    resumed_from_handle=resume_handle,
                )
                self._active_session.active_connection_id = new_conn_id
                self._active_session.connection_history.append(new_conn_rec)
                self._active_session.updated_at = datetime.now(UTC)

                if self.telemetry:
                    self.telemetry.log_connection_replaced(old_conn_id, new_conn_id)

                logger.info(
                    "connection_rotated_successfully",
                    old_conn_id=old_conn_id,
                    new_conn_id=new_conn_id,
                )
            except Exception as exc:
                logger.error("connection_rotation_failed", error=str(exc))
                if self.telemetry:
                    self.telemetry.log_session_degraded(f"Rotation failure: {exc}")
                raise ModelProviderError(f"Failed to rotate connection on GoAway: {exc}") from exc
            finally:
                self._reconnecting = False

    def attach_task(self, task_id: str) -> None:
        """Associate a background task with this voice session without coupling lifecycles."""
        if self._active_session and task_id not in self._active_session.attached_task_ids:
            self._active_session.attached_task_ids.append(task_id)

    def detach_task(self, task_id: str) -> None:
        """Disassociate a completed task without ending the voice session."""
        if self._active_session and task_id in self._active_session.attached_task_ids:
            self._active_session.attached_task_ids.remove(task_id)

    async def close_session(self) -> None:
        """Gracefully close the current voice session and its provider connection."""
        async with self._lock:
            if self._active_session:
                self._active_session.is_active = False
                if self._active_session.connection_history:
                    self._active_session.connection_history[-1].disconnected_at = datetime.now(UTC)

            await self.adapter.close()

            if self.telemetry:
                self.telemetry.log_session_closed()

            self._active_session = None
            self._session_config = None
