"""JARVIS Non-Elevation Axiom and Information-Flow Control Rules.

Implements the formal FIDES invariants:
1. Non-Elevation Axiom: Model reasoning or unverified transformations can NEVER
   elevate an UNTRUSTED label to USER_CONTROLLED or SYSTEM_TRUSTED.
2. Contagion Invariant: Mixing any UNTRUSTED input with higher-integrity data
   yields UNTRUSTED aggregate data.
3. Non-Declassification: Confidentiality cannot be lowered without explicit,
   authorized sanitization/redaction.
"""

from jarvis.core.exceptions import TrustElevationError
from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.trust.taxonomy import TrustLevel, get_trust_rank


class ElevationPolicy:
    """Enforces non-elevation invariants across state and data transformations."""

    @staticmethod
    def assert_non_elevation(
        source_integrity: IntegrityLabel,
        proposed_integrity: IntegrityLabel,
        is_cryptographic_verification: bool = False,
        is_independent_verifier: bool = False,
        actor_description: str = "model",
    ) -> None:
        """Enforce the non-elevation axiom.

        Raises TrustElevationError if an unauthorized actor or unverified transformation
        attempts to elevate integrity.
        """
        if proposed_integrity.rank > source_integrity.rank and not (
            is_cryptographic_verification or is_independent_verifier
        ):
            raise TrustElevationError(
                f"Non-Elevation Axiom Violated: Cannot elevate integrity from "
                f"'{source_integrity.value}' to '{proposed_integrity.value}' "
                f"by {actor_description}. Reasoning or unverified claims cannot "
                f"confer trust."
            )

    @staticmethod
    def assert_trust_level_elevation(
        source_trust: TrustLevel,
        proposed_trust: TrustLevel,
        is_verified: bool = False,
        actor_description: str = "model",
    ) -> None:
        """Enforce trust taxonomy non-elevation."""
        if get_trust_rank(proposed_trust) > get_trust_rank(source_trust) and not is_verified:
            raise TrustElevationError(
                f"Trust Elevation Violated: Cannot elevate trust level from "
                f"'{source_trust.value}' to '{proposed_trust.value}' "
                f"by {actor_description} without independent cryptographic verification."
            )

    @staticmethod
    def assert_non_declassification(
        source_confidentiality: ConfidentialityLabel,
        proposed_confidentiality: ConfidentialityLabel,
        is_authorized_sanitizer: bool = False,
    ) -> None:
        """Ensure sensitive data is not arbitrarily downgraded in confidentiality."""
        if (
            proposed_confidentiality.rank < source_confidentiality.rank
            and not is_authorized_sanitizer
        ):
            raise TrustElevationError(
                f"Confidentiality Declassification Blocked: Cannot lower sensitivity from "
                f"'{source_confidentiality.value}' to '{proposed_confidentiality.value}' "
                f"without authorized redaction or declassification."
            )
