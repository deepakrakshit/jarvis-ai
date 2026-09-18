"""JARVIS Policy Adherence Verifier.

Validates that real-world effects comply with security policies, autonomy levels,
and authorization boundaries post-execution.
"""

import hashlib
import json
from datetime import UTC, datetime

from jarvis.core.verification.base import BaseVerifier
from jarvis.core.verification.types import (
    VerificationRequest,
    VerificationResult,
    VerificationVerdict,
    VerificationWitness,
    VerifierType,
)


class PolicyVerifier(BaseVerifier):
    """Verifies that an execution respected authorization invariants and boundaries."""

    @property
    def verifier_type(self) -> VerifierType:
        return VerifierType.POLICY

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        """Inspect execution parameters and target for security boundary violations."""
        now = datetime.now(UTC)
        discrepancies: list[str] = []

        # Check target resource authorization
        target = request.target_resource or ""
        forbidden_substrings = [
            ".env",
            "id_rsa",
            "private_key",
            "/etc/shadow",
            "sessions.json",
            "conversations.json",
        ]
        for forbidden in forbidden_substrings:
            if forbidden in target:
                discrepancies.append(
                    f"Action targeted sensitive or protected resource: '{forbidden}'"
                )

        # Check argument boundaries
        args_str = json.dumps(request.arguments, default=str)
        for forbidden in forbidden_substrings:
            if forbidden in args_str:
                discrepancies.append(
                    f"Arguments contained reference to protected resource: '{forbidden}'"
                )

        digest = hashlib.sha256(args_str.encode("utf-8")).hexdigest()
        verified = len(discrepancies) == 0

        return VerificationResult(
            verdict=VerificationVerdict.VERIFIED if verified else VerificationVerdict.REFUTED,
            verified=verified,
            verification_method="policy_boundary_verification",
            observed_state_hash=digest,
            witness=VerificationWitness(
                witness_type="policy_attestation",
                witness_uri=target or "policy_boundary",
                observed_hash=digest,
            ),
            details={"verifier_name": "PolicyVerifier", "target": target},
            discrepancies=discrepancies,
            verified_at=now,
        )
