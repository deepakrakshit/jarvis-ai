"""JARVIS Verifier Registry and Composite Dispatcher.

Directs verification requests to the specialized verifier (State, Execution, Evidence,
Policy, or Semantic) based on capability metadata and execution characteristics.
"""

from jarvis.core.capabilities.manifest import CapabilityManifest, ToolType
from jarvis.core.verification.base import BaseVerifier
from jarvis.core.verification.evidence_verifier import EvidenceVerifier
from jarvis.core.verification.execution import ExecutionVerifier
from jarvis.core.verification.policy import PolicyVerifier
from jarvis.core.verification.semantic import SemanticVerifier
from jarvis.core.verification.state import StateVerifier
from jarvis.core.verification.types import (
    VerificationRequest,
    VerificationResult,
    VerifierType,
)


class VerifierRegistry:
    """Central registry and intelligent dispatcher for capability verifiers."""

    def __init__(self) -> None:
        self._verifiers: dict[VerifierType, BaseVerifier] = {}
        # Register standard verifier suite
        self.register(StateVerifier())
        self.register(ExecutionVerifier())
        self.register(SemanticVerifier())
        self.register(PolicyVerifier())
        self.register(EvidenceVerifier())

    def register(self, verifier: BaseVerifier) -> None:
        """Register a verifier implementation for its classified type."""
        self._verifiers[verifier.verifier_type] = verifier

    def get_verifier(self, v_type: VerifierType) -> BaseVerifier | None:
        """Retrieve a specific verifier by type."""
        return self._verifiers.get(v_type)

    def resolve_verifier(
        self,
        capability_id: str,
        manifest: CapabilityManifest | None = None,
    ) -> BaseVerifier:
        """Select the optimal verifier based on capability ID and manifest attributes."""
        # 1. Check capability ID patterns
        if any(capability_id.startswith(p) for p in ("native:fs:", "fs.", "file.")):
            return self._verifiers[VerifierType.STATE]

        if any(
            capability_id.startswith(p)
            for p in ("native:shell:", "sandbox:code:", "native:code:", "shell.", "code.")
        ):
            return self._verifiers[VerifierType.EXECUTION]

        if any(capability_id.startswith(p) for p in ("native:citation:", "citation.")):
            return self._verifiers[VerifierType.EVIDENCE]

        # 2. Check manifest tool type if available
        if manifest:
            if manifest.tool_type == ToolType.SANDBOX:
                return self._verifiers[VerifierType.EXECUTION]
            if manifest.tool_type == ToolType.NATIVE and "fs" in manifest.capability_id:
                return self._verifiers[VerifierType.STATE]

        # 3. Default to StateVerifier or SemanticVerifier
        return self._verifiers.get(VerifierType.STATE) or self._verifiers[VerifierType.SEMANTIC]

    async def verify(
        self,
        request: VerificationRequest,
        manifest: CapabilityManifest | None = None,
        override_type: VerifierType | None = None,
    ) -> VerificationResult:
        """Dispatch verification check to the appropriate verifier."""
        verifier: BaseVerifier | None = None
        if override_type:
            verifier = self.get_verifier(override_type)

        if not verifier:
            verifier = self.resolve_verifier(request.capability_id, manifest)

        return await verifier.verify(request)
