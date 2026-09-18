"""JARVIS Context Management Schemas and Domain Models.

Defines the canonical 8-layer context types, artifact references, token budgets,
and pagination data structures for zero-trust context engineering.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.core.trust.taxonomy import TrustLevel


class ContextLayerType(StrEnum):
    """Canonical 8-layer context hierarchy."""

    L0_SYSTEM_POLICY = "L0_SYSTEM_POLICY"
    L1_TASK_OBJECTIVE = "L1_TASK_OBJECTIVE"
    L2_TASK_STATE = "L2_TASK_STATE"
    L3_TOOL_SCHEMAS = "L3_TOOL_SCHEMAS"
    L4_GOVERNED_MEMORIES = "L4_GOVERNED_MEMORIES"
    L5_DIALOGUE_HISTORY = "L5_DIALOGUE_HISTORY"
    L6_TOOL_OBSERVATIONS = "L6_TOOL_OBSERVATIONS"
    L7_BACKGROUND_CONTEXT = "L7_BACKGROUND_CONTEXT"


# Default layer retention priority: higher integer = higher retention priority during pruning
DEFAULT_LAYER_PRIORITY: dict[ContextLayerType, int] = {
    ContextLayerType.L0_SYSTEM_POLICY: 100,  # Invariant: Never pruned
    ContextLayerType.L1_TASK_OBJECTIVE: 90,  # Invariant: Never pruned
    ContextLayerType.L2_TASK_STATE: 80,  # Invariant: Never pruned
    ContextLayerType.L3_TOOL_SCHEMAS: 70,  # Invariant: Never pruned
    ContextLayerType.L4_GOVERNED_MEMORIES: 40,
    ContextLayerType.L5_DIALOGUE_HISTORY: 30,
    ContextLayerType.L6_TOOL_OBSERVATIONS: 20,
    ContextLayerType.L7_BACKGROUND_CONTEXT: 10,  # Pruned first
}


class ContextItem(BaseModel):
    """Discrete atomic unit of context assigned to a specific layer."""

    item_id: str = Field(default_factory=lambda: f"ctx-{uuid4()}")
    layer: ContextLayerType
    content: str
    token_count: int = 0
    priority: int = 0
    source_id: str | None = None
    trust_level: TrustLevel = TrustLevel.EXTERNAL_UNTRUSTED
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if "priority" not in data or self.priority == 0:
            self.priority = DEFAULT_LAYER_PRIORITY.get(self.layer, 0)


class ArtifactReference(BaseModel):
    """Low-bandwidth metadata reference pointing to offloaded Data-Plane content."""

    artifact_id: str = Field(default_factory=lambda: f"art-{uuid4()}")
    task_id: str
    uri: str
    file_path: str
    mime_type: str = "text/plain"
    byte_size: int
    sha256_digest: str
    token_estimate: int
    summary: str
    trust_level: TrustLevel = TrustLevel.EXTERNAL_UNTRUSTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_tool: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaginationMetadata(BaseModel):
    """Metadata describing a paginated slice of an offloaded artifact."""

    artifact_id: str
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    total_lines: int = Field(ge=0)
    has_more: bool
    next_page_token: str | None = None


class PaginatedSlice(BaseModel):
    """A deterministic window of content read from an offloaded artifact."""

    metadata: PaginationMetadata
    lines: list[str] = Field(default_factory=list)
    content: str = ""


class ContextBudget(BaseModel):
    """Allocated token budget parameters for context assembly and offloading."""

    max_total_tokens: int = Field(default=128_000, ge=64)
    reserved_output_tokens: int = Field(default=4_096, ge=0)
    max_inline_tokens: int = Field(default=1_000, ge=1)
    max_inline_bytes: int = Field(default=4_096, ge=1)
    compaction_threshold_ratio: float = Field(default=0.8, ge=0.1, le=0.99)
    layer_budgets: dict[ContextLayerType, int] = Field(default_factory=dict)

    @property
    def effective_input_budget(self) -> int:
        """Maximum tokens available for all prompt layers combined."""
        return max(0, self.max_total_tokens - self.reserved_output_tokens)

    @property
    def compaction_trigger_tokens(self) -> int:
        """Token count threshold at which compaction should be triggered."""
        return int(self.effective_input_budget * self.compaction_threshold_ratio)


class AssembledContext(BaseModel):
    """Final assembled context payload prepared for model consumption."""

    task_id: str
    items: list[ContextItem] = Field(default_factory=list)
    system_prompt: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    total_tokens: int = 0
    budget: ContextBudget
    pruned_items: list[ContextItem] = Field(default_factory=list)
    offloaded_artifacts: list[ArtifactReference] = Field(default_factory=list)
    is_compacted: bool = False
