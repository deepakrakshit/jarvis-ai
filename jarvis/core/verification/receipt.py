"""JARVIS Temporal Effect Receipt Schemas, Cryptographic Minter, and Storage Ledger.

Implements ARCHITECTURE.md Layer 16: Point-in-time effect receipts binding
logical_effect_id, task_id, capability_id, verifier verdict, and external witnesses.
"""

import hashlib
import hmac
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from jarvis.core.config import get_settings
from jarvis.core.exceptions import ReceiptTamperedError
from jarvis.core.verification.types import VerificationResult


class EffectReceipt(BaseModel):
    """Cryptographically bound, point-in-time receipt certifying an external effect."""

    receipt_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    logical_effect_id: str
    capability_id: str
    verification_method: str
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    observed_state_hash: str
    external_version_etag: str | None = None
    verification_source: str
    witness: str
    args_hash: str
    pre_state_hash: str | None = None
    post_state_hash: str | None = None
    verified: bool
    signature: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReceiptMinter:
    """Mints and cryptographically signs tamper-evident Effect Receipts."""

    def __init__(self, signing_key: str | bytes | None = None) -> None:
        if signing_key:
            self._key = signing_key.encode("utf-8") if isinstance(signing_key, str) else signing_key
        else:
            settings = get_settings()
            secret = (
                getattr(settings, "RECEIPT_SIGNING_KEY", None) or "jarvis_canonical_receipt_secret"
            )
            self._key = secret.encode("utf-8")

    def _compute_digest(
        self,
        receipt_id: UUID | str,
        task_id: UUID | str,
        logical_effect_id: str,
        capability_id: str,
        args_hash: str,
        observed_state_hash: str,
        verified: bool,
        verified_at: datetime,
    ) -> str:
        """Compute canonical HMAC-SHA256 over receipt invariant attributes."""
        canonical_msg = (
            f"{receipt_id}:{task_id}:{logical_effect_id}:{capability_id}:"
            f"{args_hash}:{observed_state_hash}:{verified}:{verified_at.isoformat()}"
        )
        return hmac.new(self._key, canonical_msg.encode("utf-8"), hashlib.sha256).hexdigest()

    def mint(
        self,
        task_id: UUID | str,
        logical_effect_id: str,
        capability_id: str,
        args_hash: str,
        verification_result: VerificationResult,
        pre_state_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EffectReceipt:
        """Mint a new cryptographically signed EffectReceipt from a verification outcome."""
        receipt_id = uuid4()
        task_uuid = UUID(str(task_id))
        now = datetime.now(UTC)

        witness_str = (
            f"{verification_result.witness.witness_type}:{verification_result.witness.witness_uri}"
            if verification_result.witness
            else "unattested"
        )
        etag = (
            verification_result.witness.observed_version_etag
            if verification_result.witness
            else None
        )

        sig = self._compute_digest(
            receipt_id=receipt_id,
            task_id=task_uuid,
            logical_effect_id=logical_effect_id,
            capability_id=capability_id,
            args_hash=args_hash,
            observed_state_hash=verification_result.observed_state_hash,
            verified=verification_result.verified,
            verified_at=now,
        )

        return EffectReceipt(
            receipt_id=receipt_id,
            task_id=task_uuid,
            logical_effect_id=logical_effect_id,
            capability_id=capability_id,
            verification_method=verification_result.verification_method,
            verified_at=now,
            observed_state_hash=verification_result.observed_state_hash,
            external_version_etag=etag,
            verification_source=verification_result.details.get(
                "verifier_name", "generic_verifier"
            ),
            witness=witness_str,
            args_hash=args_hash,
            pre_state_hash=pre_state_hash,
            post_state_hash=verification_result.observed_state_hash,
            verified=verification_result.verified,
            signature=sig,
            metadata=metadata or {},
        )

    def verify_signature(self, receipt: EffectReceipt) -> bool:
        """Validate HMAC-SHA256 signature to guarantee receipt integrity."""
        expected = self._compute_digest(
            receipt_id=receipt.receipt_id,
            task_id=receipt.task_id,
            logical_effect_id=receipt.logical_effect_id,
            capability_id=receipt.capability_id,
            args_hash=receipt.args_hash,
            observed_state_hash=receipt.observed_state_hash,
            verified=receipt.verified,
            verified_at=receipt.verified_at,
        )
        if not hmac.compare_digest(receipt.signature, expected):
            raise ReceiptTamperedError(
                f"Receipt '{receipt.receipt_id}' signature verification failed: signature does not match content."
            )
        return True


class ReceiptStore:
    """Persistent SQLite-backed repository for Effect Receipts."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path:
            self.db_path = Path(db_path).resolve()
        else:
            self.db_path = (get_settings().DATA_DIR / "receipts.db").resolve()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS effect_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    logical_effect_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    verification_method TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    observed_state_hash TEXT NOT NULL,
                    witness TEXT NOT NULL,
                    args_hash TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    signature TEXT NOT NULL,
                    receipt_json TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_receipts_task
                ON effect_receipts (task_id);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_receipts_effect
                ON effect_receipts (logical_effect_id);
                """
            )
            conn.commit()

    def record(self, receipt: EffectReceipt) -> None:
        """Persist an effect receipt into the ledger."""
        receipt_json = receipt.model_dump_json()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO effect_receipts (
                    receipt_id, task_id, logical_effect_id, capability_id,
                    verification_method, verified_at, observed_state_hash,
                    witness, args_hash, verified, signature, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(receipt.receipt_id),
                    str(receipt.task_id),
                    receipt.logical_effect_id,
                    receipt.capability_id,
                    receipt.verification_method,
                    receipt.verified_at.isoformat(),
                    receipt.observed_state_hash,
                    receipt.witness,
                    receipt.args_hash,
                    1 if receipt.verified else 0,
                    receipt.signature,
                    receipt_json,
                ),
            )
            conn.commit()

    def get(self, receipt_id: UUID | str) -> EffectReceipt | None:
        """Retrieve an effect receipt by primary receipt_id."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT receipt_json FROM effect_receipts WHERE receipt_id = ?",
                (str(receipt_id),),
            ).fetchone()
            if not row:
                return None
            data = json.loads(row["receipt_json"])
            return EffectReceipt(**data)

    def get_by_effect(self, logical_effect_id: str) -> EffectReceipt | None:
        """Retrieve receipt for a logical effect ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT receipt_json FROM effect_receipts WHERE logical_effect_id = ?",
                (logical_effect_id,),
            ).fetchone()
            if not row:
                return None
            data = json.loads(row["receipt_json"])
            return EffectReceipt(**data)

    def list_for_task(self, task_id: UUID | str) -> list[EffectReceipt]:
        """List all effect receipts minted for a task."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT receipt_json FROM effect_receipts WHERE task_id = ? ORDER BY verified_at ASC",
                (str(task_id),),
            ).fetchall()
            return [EffectReceipt(**json.loads(r["receipt_json"])) for r in rows]
