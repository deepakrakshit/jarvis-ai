"""JARVIS Semantic and Schema Verifier.

Validates structural integrity, output non-emptiness, required fields, and logical consistency.
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


class SemanticVerifier(BaseVerifier):
    """Verifies schema conformance, payload non-vacuity, and semantic structure."""

    @property
    def verifier_type(self) -> VerifierType:
        return VerifierType.SEMANTIC

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        """Evaluate structural and semantic conformance of result payload."""
        now = datetime.now(UTC)
        result = request.result
        discrepancies: list[str] = []

        if result is None:
            discrepancies.append("Output result is None")
        elif isinstance(result, str) and not result.strip():
            discrepancies.append("Output result is empty string")
        elif (
            isinstance(result, (list, dict))
            and len(result) == 0
            and request.expected_postcondition
            and request.expected_postcondition.get("allow_empty") is False
        ):
            discrepancies.append("Output container is empty when non-empty required")

        # Check required keys if specified in expected_postcondition
        if isinstance(result, dict) and request.expected_postcondition:
            required_keys = request.expected_postcondition.get("required_keys", [])
            for k in required_keys:
                if k not in result:
                    discrepancies.append(f"Missing expected key in result: '{k}'")

        res_str = json.dumps(result, sort_keys=True, default=str) if result is not None else ""
        digest = hashlib.sha256(res_str.encode("utf-8")).hexdigest()
        verified = len(discrepancies) == 0

        return VerificationResult(
            verdict=VerificationVerdict.VERIFIED if verified else VerificationVerdict.REFUTED,
            verified=verified,
            verification_method="semantic_schema_inspection",
            observed_state_hash=digest,
            witness=VerificationWitness(
                witness_type="semantic_digest",
                witness_uri=request.capability_id,
                observed_hash=digest,
            ),
            details={
                "verifier_name": "SemanticVerifier",
                "result_type": type(result).__name__,
            },
            discrepancies=discrepancies,
            verified_at=now,
        )
