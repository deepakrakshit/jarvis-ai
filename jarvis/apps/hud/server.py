"""JARVIS Holographic HUD Server & HTTP/SSE Endpoints.

Exposes REST and Server-Sent Events (SSE) interfaces for the frontend HUD,
enabling real-time status telemetry, background task visualization,
and spoken/UI Human-in-the-Loop approval resolution.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from jarvis.apps.hud.coordinator import get_hud_coordinator
from jarvis.apps.hud.schemas import HUDState, HUDVoiceStatus


class ResolveApprovalPayload(BaseModel):
    """Payload for resolving a pending HITL authorization request."""

    approved: bool
    nonce: str | None = None


class VoiceStatusPayload(BaseModel):
    """Payload for notifying HUD of client voice connectivity transitions."""

    status: HUDVoiceStatus


router = APIRouter(prefix="/api/hud", tags=["HUD"])


@router.get("/state", response_model=HUDState)
async def get_current_hud_state() -> HUDState:
    """Retrieve instantaneous snapshot of authoritative HUD state."""
    coordinator = get_hud_coordinator()
    return coordinator.get_snapshot()


@router.get("/stream")
async def stream_hud_events() -> StreamingResponse:
    """Stream real-time HUD state updates and Control Plane telemetry via SSE."""
    coordinator = get_hud_coordinator()
    queue = coordinator.subscribe()

    async def event_generator() -> AsyncIterator[str]:
        try:
            while True:
                state = await queue.get()
                yield f"event: state_update\ndata: {state.model_dump_json()}\n\n"
        except asyncio.CancelledError:
            coordinator.unsubscribe(queue)
            raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/approvals/{approval_id}/resolve")
async def resolve_pending_approval(
    approval_id: str,
    payload: ResolveApprovalPayload,
) -> dict[str, Any]:
    """Resolve a pending Human-in-the-Loop approval with optional nonce check."""
    coordinator = get_hud_coordinator()
    success = coordinator.resolve_approval(
        approval_id=approval_id,
        approved=payload.approved,
        nonce=payload.nonce,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request '{approval_id}' not found or nonce mismatch.",
        )
    return {"approval_id": approval_id, "resolved": True, "approved": payload.approved}


@router.post("/voice/status")
async def update_voice_status(payload: VoiceStatusPayload) -> dict[str, Any]:
    """Update realtime voice connection status."""
    coordinator = get_hud_coordinator()
    coordinator.set_voice_status(payload.status)
    return {"voice_status": payload.status}


def create_hud_app() -> FastAPI:
    """Create and configure FastAPI application for JARVIS HUD."""
    app = FastAPI(title="JARVIS Holographic HUD Surface", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app
