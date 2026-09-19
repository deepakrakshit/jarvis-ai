"""Canonical Memory Contracts for JARVIS Memory Plane.

Defines MemoryRecord, MemoryType, Provenance, and TrustLevel.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class MemoryType(str, Enum):
    """Memory plane functional classification."""

    WORKING = "WORKING"  # Short-term context during task execution
    EPISODIC = "EPISODIC"  # Specific events, past task outcomes, conversation turns
    SEMANTIC = "SEMANTIC"  # Generalized facts, world knowledge, concepts
    USER_PREFERENCE = "USER_PREFERENCE"  # User preferences, habits, customizations
    PROJECT = "PROJECT"  # Codebase structure, repository state, project conventions
    LEARNED_PROCEDURE = "LEARNED_PROCEDURE"  # Multi-step skills and verified workflows
    DEVICE_STATE = "DEVICE_STATE"  # Hardware, display, OS configurations


class ProvenanceSource(str, Enum):
    """Origin source for memory records and ingested context."""

    DIRECT_USER_INSTRUCTION = "DIRECT_USER_INSTRUCTION"  # Trusted
    LOCAL_SYSTEM_FACT = "LOCAL_SYSTEM_FACT"  # Trusted
    WEB_PAGE = "WEB_PAGE"  # Untrusted external
    DOCUMENT = "DOCUMENT"  # Untrusted external
    EMAIL = "EMAIL"  # Untrusted external
    MODEL_PROPOSAL = "MODEL_PROPOSAL"  # Untrusted cognitive output
    REMOTE_NODE = "REMOTE_NODE"  # Depends on authentication
    PLUGIN = "PLUGIN"  # Depends on plugin tier


class TrustLevel(str, Enum):
    """Trust tier assigned to context and memory records."""

    TRUSTED_OPERATOR = "TRUSTED_OPERATOR"
    VERIFIED_SYSTEM = "VERIFIED_SYSTEM"
    UNTRUSTED_EXTERNAL = "UNTRUSTED_EXTERNAL"
    QUARANTINED = "QUARANTINED"


class MemoryRecord(BaseModel):
    """Durable record stored in JARVIS Memory Plane."""

    record_id: str = Field(default_factory=lambda: f"MEM-{uuid4().hex[:12].upper()}")
    memory_type: MemoryType
    key: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    provenance_source: ProvenanceSource = ProvenanceSource.DIRECT_USER_INSTRUCTION
    trust_level: TrustLevel = TrustLevel.TRUSTED_OPERATOR

    importance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    relevance_tags: List[str] = Field(default_factory=list)

    session_id: Optional[str] = None
    task_id: Optional[str] = None

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    accessed_at: datetime = Field(default_factory=utc_now)
    access_count: int = 0
