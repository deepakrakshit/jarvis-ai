"""JARVIS Capability Firewall (Least-Privilege Projection).

Dynamically computes the precise projection of tools visible to the LLM
for an active task run based on task scopes, autonomy level, and input trust origin.
Enforces that models only receive and invoke tools explicitly permitted by policy.
"""

from collections.abc import Set

from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    CapabilityStatus,
    RiskClass,
)
from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.exceptions import CapabilityFirewallError
from jarvis.core.logging import get_logger
from jarvis.core.trust.taxonomy import TrustLevel

logger = get_logger(__name__)


class CapabilityFirewall:
    """Evaluates policy constraints to project visible tools per task turn."""

    @classmethod
    def project_visible_tools(
        cls,
        registry: CapabilityRegistry,
        task_scopes: Set[str],
        autonomy_level: int = 2,
        source_trust: TrustLevel = TrustLevel.USER_INPUT,
    ) -> list[CapabilityManifest]:
        """Compute the dynamic subset of capabilities permitted for a task turn.

        Projection Rules:
        1. Operational status: Must be ACTIVE (DISABLED, DEPRECATED, QUARANTINED hidden).
        2. Scope check: All tool.required_scopes must be present in task_scopes.
        3. Trust Source check: source_trust must be in tool.allowed_trust_sources.
        4. Autonomy Level check:
           - Level 0 (Observe Only): Only READ_ONLY tools allowed.
           - Level 1-3 (Bounded): DANGEROUS tools hidden unless explicit high-risk scope granted.
           - Level 4-5 (Autonomous/Supervised): Full tool access subject to scope.
        """
        visible: list[CapabilityManifest] = []

        for cap in registry.list_active():
            # 1. Scope check (Least Privilege)
            if not set(cap.required_scopes).issubset(task_scopes):
                continue

            # 2. Trust Source check (Untrusted Data Isolation)
            if source_trust not in cap.allowed_trust_sources:
                continue

            # 3. Autonomy Level check
            if autonomy_level == 0 and cap.risk_class != RiskClass.READ_ONLY:
                continue

            if (
                autonomy_level < 4
                and cap.risk_class == RiskClass.DANGEROUS
                and "system:dangerous:override" not in task_scopes
            ):
                continue

            visible.append(cap)

        logger.debug(
            "firewall_tools_projected",
            total_registered=len(registry.list_all()),
            visible_count=len(visible),
            task_scopes=list(task_scopes),
            autonomy=autonomy_level,
            source_trust=source_trust.value,
        )
        return visible

    @classmethod
    def validate_invocation(
        cls,
        manifest: CapabilityManifest,
        task_scopes: Set[str],
        autonomy_level: int = 2,
        source_trust: TrustLevel = TrustLevel.USER_INPUT,
    ) -> None:
        """Validate whether an attempted capability invocation is permitted by the firewall.

        Raises CapabilityFirewallError if invocation violates least-privilege projection.
        """
        if manifest.status != CapabilityStatus.ACTIVE:
            raise CapabilityFirewallError(
                f"Capability '{manifest.capability_id}' cannot be invoked: "
                f"status is '{manifest.status.value}' (not ACTIVE)."
            )

        missing_scopes = set(manifest.required_scopes) - task_scopes
        if missing_scopes:
            raise CapabilityFirewallError(
                f"Capability '{manifest.capability_id}' requires missing scopes: {sorted(missing_scopes)}"
            )

        if source_trust not in manifest.allowed_trust_sources:
            raise CapabilityFirewallError(
                f"Capability '{manifest.capability_id}' cannot be invoked by source trust '{source_trust.value}'. "
                f"Allowed trust sources: {[t.value for t in manifest.allowed_trust_sources]}"
            )

        if autonomy_level == 0 and manifest.risk_class != RiskClass.READ_ONLY:
            raise CapabilityFirewallError(
                f"Capability '{manifest.capability_id}' has risk '{manifest.risk_class.value}', "
                f"which is prohibited under Autonomy Level 0 (Observe Only)."
            )

        if (
            autonomy_level < 4
            and manifest.risk_class == RiskClass.DANGEROUS
            and "system:dangerous:override" not in task_scopes
        ):
            raise CapabilityFirewallError(
                f"DANGEROUS capability '{manifest.capability_id}' is prohibited under "
                f"Autonomy Level {autonomy_level} without 'system:dangerous:override' scope."
            )
