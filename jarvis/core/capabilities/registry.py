"""JARVIS Capability Registry.

Maintains the authoritative registry of all installed capabilities with cryptographic
manifest digest verification, lifecycle tracking, and deny-by-default access.
"""

from jarvis.core.capabilities.manifest import CapabilityManifest, CapabilityStatus
from jarvis.core.exceptions import CapabilityFirewallError
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class CapabilityRegistry:
    """In-memory authoritative registry for capability manifests."""

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityManifest] = {}

    def register(self, manifest: CapabilityManifest, verify_digest: bool = True) -> None:
        """Register a new capability manifest.

        Seals the manifest with a SHA-256 digest if missing, and verifies integrity.
        """
        if not manifest.digest:
            manifest.seal()
        elif verify_digest and not manifest.verify_digest():
            raise CapabilityFirewallError(
                f"Capability manifest integrity violation for '{manifest.capability_id}'. "
                f"Computed digest does not match manifest digest header."
            )

        self._capabilities[manifest.capability_id] = manifest
        logger.info(
            "capability_registered",
            capability_id=manifest.capability_id,
            owner=manifest.owner,
            tool_type=manifest.tool_type.value,
            risk_class=manifest.risk_class.value,
            digest=manifest.digest[:12] if manifest.digest else "none",
        )

    def get(self, capability_id: str) -> CapabilityManifest | None:
        """Retrieve a capability by ID, or None if unregistered."""
        return self._capabilities.get(capability_id)

    def require(self, capability_id: str) -> CapabilityManifest:
        """Retrieve a capability by ID or raise CapabilityFirewallError (deny-by-default)."""
        manifest = self.get(capability_id)
        if not manifest:
            raise CapabilityFirewallError(
                f"Capability '{capability_id}' is not registered in the Capability Registry (deny-by-default)."
            )
        return manifest

    def unregister(self, capability_id: str) -> bool:
        """Remove a capability from the registry."""
        if capability_id in self._capabilities:
            del self._capabilities[capability_id]
            logger.info("capability_unregistered", capability_id=capability_id)
            return True
        return False

    def set_status(self, capability_id: str, status: CapabilityStatus) -> None:
        """Update the operational status of a capability."""
        manifest = self.require(capability_id)
        # Reconstruct updated manifest preserving immutability
        updated = manifest.model_copy(update={"status": status})
        updated.seal()
        self._capabilities[capability_id] = updated
        logger.info("capability_status_updated", capability_id=capability_id, status=status.value)

    def list_all(self) -> list[CapabilityManifest]:
        """Return all registered capabilities."""
        return list(self._capabilities.values())

    def list_active(self) -> list[CapabilityManifest]:
        """Return all currently ACTIVE capabilities."""
        return [c for c in self._capabilities.values() if c.status == CapabilityStatus.ACTIVE]

    def clear(self) -> None:
        """Clear all registered capabilities."""
        self._capabilities.clear()
