"""JARVIS Capability Manifest Schema and Lifecycle Status.

Defines the declarative specification for all tools (Native, MCP, A2A, Sandbox)
including security scopes, risk classes, trust prerequisites, and cryptographic digests.
"""

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from jarvis.core.ifc.sinks import SinkType
from jarvis.core.trust.taxonomy import TrustLevel


class ToolType(StrEnum):
    """Execution protocol / provider classification."""

    NATIVE = "NATIVE"
    """In-process Python high-trust capability."""

    MCP = "MCP"
    """Model Context Protocol external server tool."""

    A2A = "A2A"
    """Agent-to-Agent protocol delegated capability."""

    SANDBOX = "SANDBOX"
    """Process executed strictly within isolated OS sandbox."""


class RiskClass(StrEnum):
    """Dynamic risk categorization governing autonomy requirements."""

    READ_ONLY = "READ_ONLY"
    """No mutations or side effects (e.g., file read, clock, search)."""

    BOUNDED_MUTATION = "BOUNDED_MUTATION"
    """Mutations confined to task workspace (e.g., write temp file, local git commit)."""

    UNBOUNDED_MUTATION = "UNBOUNDED_MUTATION"
    """Mutations affecting external systems or OS files outside workspace."""

    DANGEROUS = "DANGEROUS"
    """High-impact actions (e.g., delete db, shell root exec, send financial payload)."""


class SideEffectClass(StrEnum):
    """Idempotency and rollback classification for Action Broker."""

    NONE = "NONE"
    """Zero external state alteration."""

    IDEMPOTENT = "IDEMPOTENT"
    """Safe to retry multiple times with identical parameters without state drift."""

    NON_IDEMPOTENT = "NON_IDEMPOTENT"
    """Must not be retried without compensation or unique idempotency leases."""


class CapabilityStatus(StrEnum):
    """Lifecycle status of a capability."""

    ACTIVE = "ACTIVE"
    """Operational and visible to permitted tasks."""

    DEPRECATED = "DEPRECATED"
    """Phasing out; warning issued on invocation."""

    DISABLED = "DISABLED"
    """Administratively turned off; filtered from model context."""

    QUARANTINED = "QUARANTINED"
    """Temporarily isolated due to repeated errors or anomaly detection."""


class CapabilityManifest(BaseModel):
    """Canonical declarative specification for a tool or action capability."""

    capability_id: str
    """Unique identifier (e.g. 'native:fs:read_file', 'mcp:brave_search:search')."""

    owner: str = "core"
    """Component or specialist owning this capability."""

    version: str = "1.0.0"
    """Semantic version of the capability manifest."""

    provider: str = "builtin"
    """Upstream provider or server name."""

    tool_type: ToolType = ToolType.NATIVE
    """Protocol type."""

    description: str
    """Human and model-readable description of what this tool does."""

    required_scopes: list[str] = Field(default_factory=list)
    """Permission scopes required to invoke this tool (e.g. ['filesystem:read'])."""

    input_schema: dict[str, Any] = Field(default_factory=dict)
    """JSON Schema defining the expected parameter payload."""

    output_schema: dict[str, Any] = Field(default_factory=dict)
    """JSON Schema defining the returned result structure."""

    risk_class: RiskClass = RiskClass.READ_ONLY
    """Dynamic risk classification."""

    side_effect_class: SideEffectClass = SideEffectClass.NONE
    """Idempotency classification."""

    data_requirements: list[str] = Field(default_factory=list)
    """Declared inputs or dependencies."""

    allowed_trust_sources: list[TrustLevel] = Field(
        default_factory=lambda: [TrustLevel.USER_INPUT, TrustLevel.SYSTEM_POLICY]
    )
    """Minimum trust level of inputs authorized to trigger this tool."""

    allowed_sinks: list[SinkType] = Field(default_factory=list)
    """Sinks where this tool's outputs may legally flow."""

    network_requirements: list[str] = Field(default_factory=list)
    """Outbound domains/ports required (empty if offline)."""

    sandbox_requirement: bool = False
    """Whether tool execution must run inside an isolated process sandbox."""

    approval_requirement: bool = False
    """Whether invocation requires explicit human-in-the-loop approval."""

    verification_requirement: bool = False
    """Whether invocation requires post-execution state verification receipts."""

    status: CapabilityStatus = CapabilityStatus.ACTIVE
    """Operational lifecycle state."""

    digest: str | None = None
    """Cryptographic SHA-256 digest of the canonical manifest content."""

    model_config = {"extra": "forbid"}

    def compute_digest(self) -> str:
        """Compute the deterministic SHA-256 hash of manifest fields."""
        data = self.model_dump(exclude={"digest"})
        canonical_json = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def seal(self) -> "CapabilityManifest":
        """Compute and set the digest field on this manifest."""
        self.digest = self.compute_digest()
        return self

    def verify_digest(self) -> bool:
        """Verify whether the current digest matches the manifest content."""
        if not self.digest:
            return False
        return self.digest == self.compute_digest()
