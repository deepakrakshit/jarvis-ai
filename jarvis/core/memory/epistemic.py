"""JARVIS Epistemic Calibration and Memory Trust Governor.

Enforces the non-elevation invariant: model proposals and untrusted sources cannot
silently assign VERIFIED_EXTERNAL or VERIFIED_INTERNAL epistemic status without
independent verification witnesses.
"""

from typing import Any

from jarvis.core.exceptions import MemoryEpistemicViolationError
from jarvis.core.memory.schemas import EpistemicStatus
from jarvis.core.trust.taxonomy import TrustLevel


class EpistemicGovernor:
    """Validates and enforces epistemic status calibration for memory writes."""

    @staticmethod
    def calibrate_proposal(
        status: EpistemicStatus,
        source_trust: TrustLevel,
        actor_id: str = "agent",
        has_verification_witness: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> EpistemicStatus:
        """Calibrate proposed epistemic status against actor authority and source trust."""
        # 1. External untrusted sources can NEVER exceed PROPOSED
        if source_trust == TrustLevel.EXTERNAL_UNTRUSTED:
            if status in (EpistemicStatus.VERIFIED_INTERNAL, EpistemicStatus.VERIFIED_EXTERNAL):
                raise MemoryEpistemicViolationError(
                    f"Untrusted data cannot be assigned epistemic status '{status.value}'."
                )
            return EpistemicStatus.PROPOSED

        # 2. User direct input can be recorded as OBSERVED (direct statement), but not VERIFIED_EXTERNAL
        if (
            source_trust == TrustLevel.USER_INPUT
            and status in (EpistemicStatus.VERIFIED_EXTERNAL, EpistemicStatus.VERIFIED_INTERNAL)
            and not has_verification_witness
        ):
            return EpistemicStatus.OBSERVED

        # 3. Actors cannot claim VERIFIED_EXTERNAL without an independent verification witness
        if (
            status == EpistemicStatus.VERIFIED_EXTERNAL
            and not has_verification_witness
            and actor_id not in ("verifier", "system_verifier")
        ):
            raise MemoryEpistemicViolationError(
                f"Actor '{actor_id}' cannot assign status 'VERIFIED_EXTERNAL' without an independent verifier witness."
            )

        # 4. Model-generated claims without external observation cannot exceed PROPOSED
        if (
            source_trust == TrustLevel.MODEL_GENERATED
            and not has_verification_witness
            and status in (EpistemicStatus.VERIFIED_INTERNAL, EpistemicStatus.VERIFIED_EXTERNAL)
        ):
            raise MemoryEpistemicViolationError(
                f"Model-generated assertions cannot claim status '{status.value}' without external verification."
            )

        return status
