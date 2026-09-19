"""Data Contracts and Models for Sub-Agent Specialists Architecture.

Enforces Section 29 of ARCHITECTURE.md:
- Explicit specialist roles: Research, Coding, Browser, Verify.
- Strongly-typed SubagentSpec parameterizing capabilities, context, quota, depth.
- Strongly-typed SubagentResult capturing execution outcome and verification facts.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class SubagentRole(str, Enum):
    """Specialist sub-agent execution roles."""

    RESEARCH = "RESEARCH"
    CODING = "CODING"
    BROWSER = "BROWSER"
    VERIFY = "VERIFY"


class SubagentStatus(str, Enum):
    """Sub-agent terminal and active lifecycle statuses."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class SubagentSpec(BaseModel):
    """Specification defining isolated parameters for a spawned specialist sub-agent."""

    task_id: str = Field(default_factory=lambda: f"TASK-SUB-{uuid4().hex[:8].upper()}")
    parent_task_id: str
    session_id: str
    role: SubagentRole
    goal: str
    context: Dict[str, Any] = Field(default_factory=dict)
    allowed_capabilities: List[str] = Field(default_factory=list)
    assigned_model: Optional[str] = None
    token_budget: int = 8000
    timeout_seconds: float = 60.0
    depth: int = 1
    max_depth: int = 2


class SubagentResult(BaseModel):
    """Synthesized execution report from a specialist sub-agent."""

    task_id: str
    parent_task_id: str
    role: SubagentRole
    status: SubagentStatus
    summary: str = ""
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    execution_duration_ms: float = 0.0
