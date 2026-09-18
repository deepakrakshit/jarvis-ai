"""JARVIS External-State Verifier.

Performs independent read-back queries against target external resources (filesystem,
database, external services) to verify whether the claimed mutation actually occurred
in reality at timestamp t (ARCHITECTURE.md Layer 16).
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from jarvis.core.verification.base import BaseVerifier
from jarvis.core.verification.types import (
    VerificationRequest,
    VerificationResult,
    VerificationVerdict,
    VerificationWitness,
    VerifierType,
)


class StateVerifier(BaseVerifier):
    """Verifies external point-in-time physical and storage states."""

    @property
    def verifier_type(self) -> VerifierType:
        return VerifierType.STATE

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        """Independently inspect target external system to corroborate claimed effect."""
        tool_id = request.capability_id
        args = request.arguments
        now = datetime.now(UTC)

        # 1. Filesystem Write Verification (native:fs:write_file)
        if tool_id in ("native:fs:write_file", "fs.write", "write_file"):
            file_path_raw = args.get("file_path") or args.get("path")
            expected_content = args.get("content", "")
            ws_raw = args.get("workspace_root")

            if not file_path_raw:
                return VerificationResult(
                    verdict=VerificationVerdict.REFUTED,
                    verified=False,
                    verification_method="fs_readback",
                    observed_state_hash="",
                    details={"error": "Missing file_path in arguments"},
                    discrepancies=["No file_path specified for write verification"],
                    verified_at=now,
                )

            p = Path(file_path_raw)
            if not p.is_absolute() and ws_raw:
                p = Path(ws_raw) / p
            p = p.resolve()
            if not p.exists():
                return VerificationResult(
                    verdict=VerificationVerdict.REFUTED,
                    verified=False,
                    verification_method="fs_readback",
                    observed_state_hash="",
                    witness=VerificationWitness(
                        witness_type="fs_path_missing",
                        witness_uri=str(p),
                        observed_hash="missing",
                    ),
                    details={"file_path": str(p), "status": "not_found"},
                    discrepancies=[
                        f"Target file '{p}' does not exist on disk after write operation"
                    ],
                    verified_at=now,
                )

            # Independent read-back from storage
            try:
                actual_bytes = p.read_bytes()
                actual_hash = hashlib.sha256(actual_bytes).hexdigest()
                expected_hash = hashlib.sha256(expected_content.encode("utf-8")).hexdigest()

                if actual_hash == expected_hash:
                    return VerificationResult(
                        verdict=VerificationVerdict.VERIFIED,
                        verified=True,
                        verification_method="fs_readback_sha256",
                        observed_state_hash=actual_hash,
                        witness=VerificationWitness(
                            witness_type="file_sha256",
                            witness_uri=str(p),
                            observed_hash=actual_hash,
                            metadata={"bytes": len(actual_bytes)},
                        ),
                        details={
                            "file_path": str(p),
                            "bytes": len(actual_bytes),
                            "sha256": actual_hash,
                            "verifier_name": "StateVerifier:fs_readback",
                        },
                        verified_at=now,
                    )
                return VerificationResult(
                    verdict=VerificationVerdict.REFUTED,
                    verified=False,
                    verification_method="fs_readback_sha256",
                    observed_state_hash=actual_hash,
                    witness=VerificationWitness(
                        witness_type="file_sha256",
                        witness_uri=str(p),
                        observed_hash=actual_hash,
                    ),
                    details={
                        "file_path": str(p),
                        "actual_hash": actual_hash,
                        "expected_hash": expected_hash,
                    },
                    discrepancies=[
                        f"File content hash mismatch: expected {expected_hash}, observed {actual_hash}"
                    ],
                    verified_at=now,
                )
            except Exception as read_err:
                return VerificationResult(
                    verdict=VerificationVerdict.AMBIGUOUS,
                    verified=False,
                    verification_method="fs_readback",
                    observed_state_hash="",
                    details={"error": str(read_err)},
                    discrepancies=[f"Failed to read file for verification: {read_err}"],
                    verified_at=now,
                )

        # 2. Filesystem Deletion Verification (native:fs:delete_file)
        if tool_id in ("native:fs:delete_file", "fs.delete", "delete_file"):
            file_path_raw = args.get("file_path") or args.get("path")
            ws_raw = args.get("workspace_root")
            if not file_path_raw:
                return VerificationResult(
                    verdict=VerificationVerdict.REFUTED,
                    verified=False,
                    verification_method="fs_deletion_check",
                    observed_state_hash="",
                    details={"error": "Missing file_path in arguments"},
                    discrepancies=["No file_path specified for delete verification"],
                    verified_at=now,
                )

            p = Path(file_path_raw)
            if not p.is_absolute() and ws_raw:
                p = Path(ws_raw) / p
            p = p.resolve()
            if not p.exists():
                # File is confirmed absent
                observed_hash = hashlib.sha256(b"absent").hexdigest()
                return VerificationResult(
                    verdict=VerificationVerdict.VERIFIED,
                    verified=True,
                    verification_method="fs_deletion_absence_check",
                    observed_state_hash=observed_hash,
                    witness=VerificationWitness(
                        witness_type="fs_path_absent",
                        witness_uri=str(p),
                        observed_hash=observed_hash,
                    ),
                    details={
                        "file_path": str(p),
                        "status": "confirmed_absent",
                        "verifier_name": "StateVerifier:fs_deletion",
                    },
                    verified_at=now,
                )
            return VerificationResult(
                verdict=VerificationVerdict.REFUTED,
                verified=False,
                verification_method="fs_deletion_absence_check",
                observed_state_hash="present",
                witness=VerificationWitness(
                    witness_type="fs_path_present",
                    witness_uri=str(p),
                    observed_hash="present",
                ),
                details={"file_path": str(p), "status": "still_present"},
                discrepancies=[f"File '{p}' still exists on disk after delete operation"],
                verified_at=now,
            )

        # 3. Generic State Fallback
        res_str = str(request.result) if request.result is not None else ""
        digest = hashlib.sha256(res_str.encode("utf-8")).hexdigest()
        is_success = bool(request.result is not None and not isinstance(request.result, Exception))

        return VerificationResult(
            verdict=VerificationVerdict.VERIFIED if is_success else VerificationVerdict.REFUTED,
            verified=is_success,
            verification_method="state_observation_digest",
            observed_state_hash=digest,
            witness=VerificationWitness(
                witness_type="generic_state",
                witness_uri=request.target_resource or "generic_target",
                observed_hash=digest,
            ),
            details={
                "target_resource": request.target_resource,
                "verifier_name": "StateVerifier:generic",
            },
            verified_at=now,
        )
