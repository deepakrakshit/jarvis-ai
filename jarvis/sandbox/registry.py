"""JARVIS Sandbox Provider Registry.

Resolves execution containment providers based on declarative IsolationTier,
dynamic host SystemCapabilities, profile requirements satisfaction,
and strict fail-closed enforcement.
"""

from __future__ import annotations

from jarvis.core.exceptions import SandboxBackendUnavailableError
from jarvis.core.logging import get_logger
from jarvis.sandbox.detector import evaluate_profile_satisfaction, get_system_capabilities
from jarvis.sandbox.models import (
    CapabilityStatus,
    IsolationTier,
    ProviderCategory,
    SandboxProfile,
)
from jarvis.sandbox.provider import (
    DockerSandboxProvider,
    SandboxProvider,
    TestDoubleSandboxProvider,
)

logger = get_logger(__name__)


class SandboxProviderRegistry:
    """Central registry resolving sandbox execution providers."""

    def __init__(self) -> None:
        self._providers: dict[tuple[IsolationTier, ProviderCategory], SandboxProvider] = {}
        # Register standard real and test double providers
        self._docker_provider = DockerSandboxProvider()
        self._test_double_provider = TestDoubleSandboxProvider()

        self._providers[
            (IsolationTier.TIER_1_CONTAINER, ProviderCategory.REAL_ISOLATION_PROVIDER)
        ] = self._docker_provider
        self._providers[(IsolationTier.TIER_1_CONTAINER, ProviderCategory.TEST_DOUBLE)] = (
            self._test_double_provider
        )

    def register_provider(self, provider: SandboxProvider) -> None:
        """Register a custom sandbox provider."""
        key = (provider.supported_tier, provider.provider_category)
        self._providers[key] = provider
        logger.debug(
            "sandbox_provider_registered",
            tier=provider.supported_tier.value,
            category=provider.provider_category.value,
            backend=provider.backend_type.value,
        )

    def resolve_provider(
        self,
        tier: IsolationTier = IsolationTier.TIER_1_CONTAINER,
        profile: SandboxProfile | None = None,
        allow_test_doubles: bool = False,
    ) -> SandboxProvider:
        """Resolve a sandbox provider for the requested isolation tier and profile.

        Enforces the fail-closed invariant:
        1. Checks profile requirement satisfaction against system capabilities.
        2. If Tier-1, Tier-2, or Tier-3 isolation is requested and no real isolation provider
           is operational, raises SandboxBackendUnavailableError unless
           a test double is explicitly authorized for contract testing.
        3. Under no circumstances does it silently fall back to an unisolated host process.
        """
        # 1. Reject unisolated host execution tier
        if tier == IsolationTier.TIER_0_LOCAL:
            raise SandboxBackendUnavailableError(
                "TIER_0_LOCAL represents unisolated host execution and cannot be resolved as an isolated sandbox provider."
            )

        # 2. Check tier consistency with profile
        if profile is not None and profile.isolation_tier != tier:
            raise SandboxBackendUnavailableError(
                f"Fail-closed invariant enforced: Requested tier '{tier.value}' does not match profile tier '{profile.isolation_tier.value}'."
            )

        caps = get_system_capabilities()

        # 3. Profile requirement satisfaction evaluation
        if profile is not None:
            sat_result = evaluate_profile_satisfaction(profile, caps)
            if not sat_result.is_satisfied and not allow_test_doubles:
                reasons = "; ".join(sat_result.unsatisfied_reasons)
                logger.error(
                    "sandbox_profile_unsatisfied_fail_closed",
                    profile_id=profile.profile_id,
                    reasons=sat_result.unsatisfied_reasons,
                )
                raise SandboxBackendUnavailableError(
                    f"Fail-closed invariant enforced: Sandbox profile '{profile.profile_id}' requirements are unsatisfied ({reasons}). "
                    "Automatic fallback to an unisolated host subprocess is strictly prohibited."
                )

        # 4. Resolve provider for tier
        category = (
            ProviderCategory.TEST_DOUBLE
            if allow_test_doubles
            else ProviderCategory.REAL_ISOLATION_PROVIDER
        )
        key = (tier, category)

        # For production Tier 1, check daemon availability
        if (
            tier == IsolationTier.TIER_1_CONTAINER
            and not allow_test_doubles
            and caps.docker_daemon_available != CapabilityStatus.SUPPORTED
        ):
            reasons = (
                "; ".join(caps.diagnostics) if caps.diagnostics else "Container engine is inactive"
            )
            logger.error("sandbox_fail_closed", tier=tier.value, diagnostics=caps.diagnostics)
            raise SandboxBackendUnavailableError(
                f"Fail-closed invariant enforced: Required isolation tier '{tier.value}' is unavailable ({reasons}). "
                "Automatic unisolated host fallback is strictly prohibited."
            )

        provider = self._providers.get(key)
        if provider is not None:
            return provider

        # If test double requested but not registered for this exact tier
        if allow_test_doubles:
            raise SandboxBackendUnavailableError(
                f"Fail-closed invariant enforced: No test double provider registered for requested tier '{tier.value}'."
            )

        # If real provider not available
        reasons = "; ".join(caps.diagnostics) if caps.diagnostics else "Driver not available"
        raise SandboxBackendUnavailableError(
            f"Fail-closed invariant enforced: Required isolation tier '{tier.value}' is unavailable ({reasons}). "
            "Automatic unisolated host fallback is strictly prohibited."
        )


_GLOBAL_REGISTRY: SandboxProviderRegistry | None = None


def get_sandbox_registry() -> SandboxProviderRegistry:
    """Retrieve global singleton sandbox provider registry."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = SandboxProviderRegistry()
    return _GLOBAL_REGISTRY
