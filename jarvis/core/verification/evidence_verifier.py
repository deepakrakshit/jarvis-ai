"""JARVIS Evidence and Citation Verifier.

Validates empirical backing, external publication sources, and epistemic claims
using authoritative multi-provider bibliographic validation.
"""

import hashlib
from datetime import UTC, datetime

from jarvis.core.verification.base import BaseVerifier
from jarvis.core.verification.citation import verify_citation_async
from jarvis.core.verification.types import (
    VerificationRequest,
    VerificationResult,
    VerificationVerdict,
    VerificationWitness,
    VerifierType,
)


class EvidenceVerifier(BaseVerifier):
    """Verifies empirical evidence, research citations, and external truth claims."""

    @property
    def verifier_type(self) -> VerifierType:
        return VerifierType.EVIDENCE

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        """Inspect evidence or citation claims against authoritative providers."""
        now = datetime.now(UTC)
        args = request.arguments

        query = args.get("query")
        doi = args.get("doi")
        url = args.get("url")
        claimed_title = args.get("claimed_title") or args.get("title")

        if not any([query, doi, url, claimed_title]):
            # No citation arguments to verify
            res_str = str(request.result)
            digest = hashlib.sha256(res_str.encode("utf-8")).hexdigest()
            return VerificationResult(
                verdict=VerificationVerdict.VERIFIED,
                verified=True,
                verification_method="evidence_fallback_digest",
                observed_state_hash=digest,
                witness=VerificationWitness(
                    witness_type="evidence_digest",
                    witness_uri="general_evidence",
                    observed_hash=digest,
                ),
                details={"verifier_name": "EvidenceVerifier:fallback"},
                verified_at=now,
            )

        # Execute citation validation
        cit_payload = {
            "query": query,
            "doi": doi,
            "url": url,
            "claimed_authors": args.get("claimed_authors"),
            "claimed_year": args.get("claimed_year"),
            "claimed_title": claimed_title,
        }
        validation_res = await verify_citation_async(citation=cit_payload)

        observed_hash = (
            validation_res.reconciled_record.metadata_digest
            if validation_res.reconciled_record
            else hashlib.sha256(str(validation_res.state.value).encode("utf-8")).hexdigest()
        )

        witness_uri = (
            validation_res.reconciled_record.source_id
            if validation_res.reconciled_record
            else (doi or url or query or "unknown_source")
        )

        is_verified = validation_res.is_valid
        verdict = (
            VerificationVerdict.VERIFIED
            if is_verified
            else (
                VerificationVerdict.REFUTED
                if validation_res.state.value in ("INVALID", "CONFLICT")
                else VerificationVerdict.AMBIGUOUS
            )
        )

        return VerificationResult(
            verdict=verdict,
            verified=is_verified,
            verification_method="multi_provider_bibliographic_reconciliation",
            observed_state_hash=observed_hash,
            witness=VerificationWitness(
                witness_type="bibliographic_record",
                witness_uri=witness_uri,
                observed_hash=observed_hash,
                metadata={"matched_providers": validation_res.matched_providers},
            ),
            details={
                "validation_state": validation_res.state.value,
                "issues": validation_res.issues,
                "matched_providers": validation_res.matched_providers,
                "verifier_name": "EvidenceVerifier:citation",
            },
            discrepancies=validation_res.issues,
            verified_at=now,
        )
