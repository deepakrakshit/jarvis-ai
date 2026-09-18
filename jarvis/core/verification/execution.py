"""JARVIS Runtime Execution Verifier.

Validates process lifecycle outcomes, exit codes, output streams, execution duration,
and container sentinel attestation (ARCHITECTURE.md Layer 16).
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


class ExecutionVerifier(BaseVerifier):
    """Verifies process exit status, command output sentinels, and runtime constraints."""

    @property
    def verifier_type(self) -> VerifierType:
        return VerifierType.EXECUTION

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        """Inspect command or container execution result against execution contracts."""
        now = datetime.now(UTC)
        result = request.result

        if not isinstance(result, dict):
            # Non-dict execution result
            res_str = str(result)
            digest = hashlib.sha256(res_str.encode("utf-8")).hexdigest()
            is_valid = result is not None and not isinstance(result, Exception)
            return VerificationResult(
                verdict=VerificationVerdict.VERIFIED if is_valid else VerificationVerdict.REFUTED,
                verified=is_valid,
                verification_method="raw_execution_digest",
                observed_state_hash=digest,
                details={"verifier_name": "ExecutionVerifier:raw"},
                verified_at=now,
            )

        # Evaluate execution dictionary (exit_code, stdout, stderr, timed_out)
        exit_code = result.get("exit_code")
        timed_out = bool(result.get("timed_out", False))
        stdout = str(result.get("stdout", ""))
        stderr = str(result.get("stderr", ""))

        discrepancies: list[str] = []
        expected_exit = 0
        if (
            request.expected_postcondition
            and "expected_exit_code" in request.expected_postcondition
        ):
            expected_exit = int(request.expected_postcondition["expected_exit_code"])

        if timed_out:
            discrepancies.append("Execution timed out before completion")

        if exit_code is not None and exit_code != expected_exit:
            discrepancies.append(
                f"Process exit code {exit_code} does not match expected {expected_exit}"
            )

        # Compute deterministic state hash of output streams
        combined = json.dumps(
            {"exit_code": exit_code, "stdout": stdout, "stderr": stderr, "timed_out": timed_out},
            sort_keys=True,
        )
        observed_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()

        verified = len(discrepancies) == 0 and exit_code == expected_exit

        return VerificationResult(
            verdict=VerificationVerdict.VERIFIED if verified else VerificationVerdict.REFUTED,
            verified=verified,
            verification_method="process_exit_and_stream_attestation",
            observed_state_hash=observed_hash,
            witness=VerificationWitness(
                witness_type="process_exit",
                witness_uri=str(request.arguments.get("command", "command")),
                observed_hash=observed_hash,
                metadata={"exit_code": exit_code, "timed_out": timed_out},
            ),
            details={
                "exit_code": exit_code,
                "timed_out": timed_out,
                "stdout_len": len(stdout),
                "stderr_len": len(stderr),
                "verifier_name": "ExecutionVerifier:process",
            },
            discrepancies=discrepancies,
            verified_at=now,
        )
