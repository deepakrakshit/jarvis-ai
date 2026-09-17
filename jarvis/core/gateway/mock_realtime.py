"""Deterministic Mock Realtime Adapter for Offline Testing and Chaos Simulations.

Allows comprehensive validation of bidirectional streaming, GoAway recovery,
session resumption, Extended Thinking status transitions, tool calls, and barge-in
without consuming live provider quota or requiring network access.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from jarvis.core.gateway.realtime import (
    LiveAudioChunk,
    LiveEvent,
    LiveEventType,
    LiveGoAway,
    LiveInteractionStatus,
    LiveResumptionUpdate,
    LiveSessionConfig,
    LiveToolCall,
    LiveToolResponse,
    RealtimeModelAdapter,
)


class MockRealtimeAdapter(RealtimeModelAdapter):
    """Deterministic, async-safe mock implementation of RealtimeModelAdapter."""

    def __init__(self, default_model_id: str = "gemini-3.8-live") -> None:
        self.default_model_id = default_model_id
        self._connected: bool = False
        self.config: LiveSessionConfig | None = None
        self.latest_resumption_handle: str | None = None

        # Inspection buffers for assertions
        self.sent_texts: list[str] = []
        self.sent_audio: list[bytes] = []
        self.sent_tool_responses: list[LiveToolResponse] = []
        self.interrupt_count: int = 0
        self.connect_count: int = 0
        self.close_count: int = 0
        self.fail_next_connect: bool = False
        self.connect_exception: Exception | None = None

        # Event queue feeding receive_events()
        self._event_queue: asyncio.Queue[LiveEvent | None] = asyncio.Queue()

    async def connect(self, config: LiveSessionConfig) -> None:
        """Simulate connecting to realtime provider."""
        if self.fail_next_connect:
            self.fail_next_connect = False
            exc = self.connect_exception or ConnectionResetError("Simulated connection failure")
            raise exc

        self.connected = True
        self.config = config
        self.connect_count += 1
        if config.resumption_handle:
            self.latest_resumption_handle = config.resumption_handle

    async def send_text(self, text: str, end_of_turn: bool = True) -> None:
        """Record outbound user text."""
        self.sent_texts.append(text)

    async def send_audio(self, pcm_data: bytes, end_of_turn: bool = False) -> None:
        """Record outbound user audio chunks."""
        self.sent_audio.append(pcm_data)

    async def send_tool_response(self, response: LiveToolResponse) -> None:
        """Record outbound verified function responses."""
        self.sent_tool_responses.append(response)

    async def interrupt(self) -> None:
        """Record client interruption / barge-in signal."""
        self.interrupt_count += 1

    async def close(self) -> None:
        """Simulate connection teardown."""
        self.connected = False
        self.close_count += 1
        await self._event_queue.put(None)  # Signal end of stream

    def get_latest_resumption_handle(self) -> str | None:
        """Return tracked resumption token."""
        return self.latest_resumption_handle

    @property
    def connected(self) -> bool:
        """Return connection status."""
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = value

    async def receive_events(self) -> AsyncIterator[LiveEvent]:
        """Stream queued mock events asynchronously."""
        while True:
            event = await self._event_queue.get()
            if event is None:
                break
            yield event

    # --- Test Control & Scenario Injectors ---

    def queue_event(self, event: LiveEvent) -> None:
        """Enqueue an arbitrary event for receive_events to emit."""
        self._event_queue.put_nowait(event)

    def queue_speech(self, text: str, pcm_bytes: bytes = b"\x00\x01" * 100) -> None:
        """Enqueue a speech turn with text delta, audio chunk, and turn completion."""
        self.queue_event(LiveEvent(event_type=LiveEventType.TEXT_DELTA, payload=text))
        self.queue_event(
            LiveEvent(
                event_type=LiveEventType.AUDIO_CHUNK,
                payload=LiveAudioChunk(data=pcm_bytes, sample_rate=24000),
            )
        )
        self.queue_event(
            LiveEvent(event_type=LiveEventType.TURN_COMPLETE, payload={"turn_complete": True})
        )

    def queue_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        call_id: str | None = None,
        is_non_blocking: bool = True,
    ) -> str:
        """Enqueue a tool call proposal from the model."""
        cid = call_id or f"call_{uuid4().hex[:8]}"
        self.queue_event(
            LiveEvent(
                event_type=LiveEventType.TOOL_CALL,
                payload=LiveToolCall(
                    call_id=cid,
                    name=name,
                    arguments=arguments,
                    is_non_blocking=is_non_blocking,
                ),
            )
        )
        return cid

    def queue_go_away(self, time_left_seconds: float = 30.0) -> None:
        """Enqueue a GoAway connection rotation warning."""
        self.queue_event(
            LiveEvent(
                event_type=LiveEventType.GO_AWAY,
                payload=LiveGoAway(time_left_seconds=time_left_seconds),
            )
        )

    def queue_resumption_update(self, new_handle: str, resumable: bool = True) -> None:
        """Enqueue a session resumption token update."""
        self.latest_resumption_handle = new_handle
        self.queue_event(
            LiveEvent(
                event_type=LiveEventType.RESUMPTION_UPDATE,
                payload=LiveResumptionUpdate(handle=new_handle, resumable=resumable),
            )
        )

    def queue_status_change(self, status: LiveInteractionStatus) -> None:
        """Enqueue an interaction status change for Extended Thinking."""
        self.queue_event(
            LiveEvent(
                event_type=LiveEventType.STATUS_CHANGE,
                payload={"status": status.value},
            )
        )

    def queue_error(self, message: str) -> None:
        """Enqueue an error event."""
        self.queue_event(
            LiveEvent(
                event_type=LiveEventType.ERROR,
                payload={"error": message},
            )
        )
