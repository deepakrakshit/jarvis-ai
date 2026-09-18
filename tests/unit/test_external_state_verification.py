"""Unit & Contract Tests for JARVIS External-State Verification & Temporal Effect Receipts.

Validates the pluggable verifier suite (State, Execution, Semantic, Policy, Evidence),
cryptographic Effect Receipt minting, tamper detection, SQLite ledger persistence,
and Action Broker verification enforcement.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.broker.broker import ActionBroker
from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    RiskClass,
    SideEffectClass,
    ToolType,
)
from jarvis.core.exceptions import (
    ReceiptTamperedError,
    VerificationFailureError,
)
from jarvis.core.policy.decision import EffectAuthorization
from jarvis.core.trust.taxonomy import TrustLevel
from jarvis.core.verification.execution import ExecutionVerifier
from jarvis.core.verification.policy import PolicyVerifier
from jarvis.core.verification.receipt import ReceiptMinter, ReceiptStore
from jarvis.core.verification.semantic import SemanticVerifier
from jarvis.core.verification.state import StateVerifier
from jarvis.core.verification.types import (
    VerificationRequest,
    VerificationVerdict,
)


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Isolated temporary workspace."""
    p = tmp_path / "verification_workspace"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def receipt_store(tmp_path: Path) -> ReceiptStore:
    """Isolated SQLite receipt repository."""
    return ReceiptStore(db_path=tmp_path / "test_receipts.db")


@pytest.fixture
def receipt_minter() -> ReceiptMinter:
    """Receipt minter with test signing key."""
    return ReceiptMinter(signing_key="test_cryptographic_secret_key_12345")


# 1. StateVerifier Tests


@pytest.mark.asyncio
async def test_state_verifier_file_write_success(temp_dir: Path) -> None:
    """Verify StateVerifier corroborates real file existence and SHA-256 hash."""
    verifier = StateVerifier()
    test_file = temp_dir / "output.txt"
    content = "Hello, JARVIS Verification!"
    test_file.write_text(content, encoding="utf-8")

    req = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_test_write",
        capability_id="native:fs:write_file",
        arguments={"file_path": str(test_file), "content": content},
        result={"path": str(test_file), "bytes_written": len(content)},
    )

    res = await verifier.verify(req)
    assert res.verdict == VerificationVerdict.VERIFIED
    assert res.verified is True
    assert res.witness is not None
    assert res.witness.witness_type == "file_sha256"
    assert res.witness.observed_hash == hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_state_verifier_file_write_missing_file(temp_dir: Path) -> None:
    """Verify StateVerifier refutes write when target file does not exist on disk."""
    verifier = StateVerifier()
    missing_file = temp_dir / "non_existent.txt"

    req = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_test_missing",
        capability_id="native:fs:write_file",
        arguments={"file_path": str(missing_file), "content": "phantom"},
        result={"path": str(missing_file), "bytes_written": 7},
    )

    res = await verifier.verify(req)
    assert res.verdict == VerificationVerdict.REFUTED
    assert res.verified is False
    assert any("does not exist" in d for d in res.discrepancies)


@pytest.mark.asyncio
async def test_state_verifier_file_write_content_mismatch(temp_dir: Path) -> None:
    """Verify StateVerifier refutes write when observed content differs from expected."""
    verifier = StateVerifier()
    test_file = temp_dir / "tampered.txt"
    test_file.write_text("actual disk content", encoding="utf-8")

    req = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_test_mismatch",
        capability_id="native:fs:write_file",
        arguments={"file_path": str(test_file), "content": "expected content"},
        result={"path": str(test_file), "bytes_written": 16},
    )

    res = await verifier.verify(req)
    assert res.verdict == VerificationVerdict.REFUTED
    assert res.verified is False
    assert any("hash mismatch" in d for d in res.discrepancies)


@pytest.mark.asyncio
async def test_state_verifier_file_deletion(temp_dir: Path) -> None:
    """Verify StateVerifier correctly validates file absence after deletion."""
    verifier = StateVerifier()
    target_file = temp_dir / "to_delete.txt"

    # 1. File does not exist -> Confirmed absent -> VERIFIED
    req_absent = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_del_absent",
        capability_id="native:fs:delete_file",
        arguments={"file_path": str(target_file)},
        result={"deleted": True},
    )
    res_absent = await verifier.verify(req_absent)
    assert res_absent.verdict == VerificationVerdict.VERIFIED
    assert res_absent.verified is True

    # 2. File still exists on disk -> REFUTED
    target_file.write_text("still here", encoding="utf-8")
    req_present = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_del_present",
        capability_id="native:fs:delete_file",
        arguments={"file_path": str(target_file)},
        result={"deleted": True},
    )
    res_present = await verifier.verify(req_present)
    assert res_present.verdict == VerificationVerdict.REFUTED
    assert res_present.verified is False


# 2. ExecutionVerifier Tests


@pytest.mark.asyncio
async def test_execution_verifier_success() -> None:
    """Verify ExecutionVerifier validates zero exit code and absence of timeout."""
    verifier = ExecutionVerifier()

    req = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_exec_ok",
        capability_id="native:shell:execute",
        arguments={"command": "echo test"},
        result={"exit_code": 0, "stdout": "test\n", "stderr": "", "timed_out": False},
    )
    res = await verifier.verify(req)
    assert res.verdict == VerificationVerdict.VERIFIED
    assert res.verified is True
    assert res.witness is not None
    assert res.witness.witness_type == "process_exit"


@pytest.mark.asyncio
async def test_execution_verifier_non_zero_exit() -> None:
    """Verify ExecutionVerifier refutes non-zero exit code."""
    verifier = ExecutionVerifier()

    req = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_exec_fail",
        capability_id="native:shell:execute",
        arguments={"command": "exit 1"},
        result={"exit_code": 1, "stdout": "", "stderr": "error", "timed_out": False},
    )
    res = await verifier.verify(req)
    assert res.verdict == VerificationVerdict.REFUTED
    assert res.verified is False
    assert any("exit code 1" in d for d in res.discrepancies)


@pytest.mark.asyncio
async def test_execution_verifier_timed_out() -> None:
    """Verify ExecutionVerifier refutes timed out process."""
    verifier = ExecutionVerifier()

    req = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_exec_timeout",
        capability_id="native:shell:execute",
        arguments={"command": "sleep 100"},
        result={"exit_code": None, "stdout": "", "stderr": "", "timed_out": True},
    )
    res = await verifier.verify(req)
    assert res.verdict == VerificationVerdict.REFUTED
    assert res.verified is False
    assert any("timed out" in d for d in res.discrepancies)


# 3. SemanticVerifier Tests


@pytest.mark.asyncio
async def test_semantic_verifier_required_keys() -> None:
    """Verify SemanticVerifier checks expected dictionary keys."""
    verifier = SemanticVerifier()

    # Success
    req_ok = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_sem_ok",
        capability_id="native:clock:get_time",
        result={"iso": "2026-09-18T12:00:00Z", "utc": "12:00:00"},
        expected_postcondition={"required_keys": ["iso", "utc"]},
    )
    res_ok = await verifier.verify(req_ok)
    assert res_ok.verdict == VerificationVerdict.VERIFIED

    # Missing key
    req_missing = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_sem_missing",
        capability_id="native:clock:get_time",
        result={"iso": "2026-09-18T12:00:00Z"},
        expected_postcondition={"required_keys": ["iso", "utc"]},
    )
    res_missing = await verifier.verify(req_missing)
    assert res_missing.verdict == VerificationVerdict.REFUTED
    assert any("Missing expected key" in d for d in res_missing.discrepancies)


# 4. PolicyVerifier Tests


@pytest.mark.asyncio
async def test_policy_verifier_detects_sensitive_target() -> None:
    """Verify PolicyVerifier flags actions touching sensitive files."""
    verifier = PolicyVerifier()

    req_unsafe = VerificationRequest(
        task_id=uuid4(),
        logical_effect_id="eff_pol_unsafe",
        capability_id="native:fs:read_file",
        target_resource="/path/to/.env",
        arguments={"file_path": "/path/to/.env"},
        result="SECRET_KEY=123",
    )
    res_unsafe = await verifier.verify(req_unsafe)
    assert res_unsafe.verdict == VerificationVerdict.REFUTED
    assert any("targeted sensitive or protected resource" in d for d in res_unsafe.discrepancies)


# 5. ReceiptMinter & EffectReceipt Cryptographic Integrity Tests


def test_receipt_minter_and_tamper_detection(receipt_minter: ReceiptMinter) -> None:
    """Verify cryptographic receipt signing and detection of tampering."""
    from jarvis.core.verification.types import VerificationResult, VerificationWitness

    v_result = VerificationResult(
        verdict=VerificationVerdict.VERIFIED,
        verified=True,
        verification_method="fs_readback_sha256",
        observed_state_hash="abc123hash",
        witness=VerificationWitness(
            witness_type="file_sha256",
            witness_uri="file:///test.txt",
            observed_hash="abc123hash",
        ),
    )

    receipt = receipt_minter.mint(
        task_id=uuid4(),
        logical_effect_id="eff_mint_test",
        capability_id="native:fs:write_file",
        args_hash="args_hash_xyz",
        verification_result=v_result,
    )

    assert receipt.receipt_id is not None
    assert receipt.signature != ""
    assert receipt.verified is True

    # Signature verification succeeds for unaltered receipt
    assert receipt_minter.verify_signature(receipt) is True

    # Tampering with observed_state_hash raises ReceiptTamperedError
    tampered_receipt = receipt.model_copy(update={"observed_state_hash": "tampered_hash"})
    with pytest.raises(ReceiptTamperedError):
        receipt_minter.verify_signature(tampered_receipt)

    # Tampering with verified status raises ReceiptTamperedError
    tampered_status = receipt.model_copy(update={"verified": False})
    with pytest.raises(ReceiptTamperedError):
        receipt_minter.verify_signature(tampered_status)


def test_receipt_store_persistence(
    receipt_store: ReceiptStore, receipt_minter: ReceiptMinter
) -> None:
    """Verify SQLite persistence and lookup of effect receipts."""
    from jarvis.core.verification.types import VerificationResult

    task_id = uuid4()
    v_result = VerificationResult(
        verdict=VerificationVerdict.VERIFIED,
        verified=True,
        verification_method="test_method",
        observed_state_hash="hash999",
    )
    receipt = receipt_minter.mint(
        task_id=task_id,
        logical_effect_id="eff_store_test",
        capability_id="native:fs:write_file",
        args_hash="args999",
        verification_result=v_result,
    )

    receipt_store.record(receipt)

    # Lookup by receipt_id
    fetched = receipt_store.get(receipt.receipt_id)
    assert fetched is not None
    assert fetched.logical_effect_id == "eff_store_test"
    assert fetched.signature == receipt.signature

    # Lookup by logical_effect_id
    fetched_eff = receipt_store.get_by_effect("eff_store_test")
    assert fetched_eff is not None
    assert fetched_eff.receipt_id == receipt.receipt_id

    # List for task
    task_receipts = receipt_store.list_for_task(task_id)
    assert len(task_receipts) == 1
    assert task_receipts[0].receipt_id == receipt.receipt_id


# 6. ActionBroker End-to-End Verification Integration


@pytest.mark.asyncio
async def test_action_broker_with_verified_manifest_success(
    temp_dir: Path, receipt_store: ReceiptStore
) -> None:
    """Verify ActionBroker executes tool, validates state, and mints effect receipt."""
    broker = ActionBroker(receipt_store=receipt_store)
    test_file = temp_dir / "verified_action.txt"
    content = "ActionBroker Verified Content"

    manifest = CapabilityManifest(
        capability_id="native:fs:write_file",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.NATIVE,
        description="Write file with verification requirement.",
        required_scopes=["file:write"],
        risk_class=RiskClass.BOUNDED_MUTATION,
        side_effect_class=SideEffectClass.IDEMPOTENT,
        allowed_trust_sources=[TrustLevel.USER_INPUT],
        sandbox_requirement=False,
        verification_requirement=True,
    )

    async def executor(args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["file_path"])
        p.write_text(args["content"], encoding="utf-8")
        return {"file_path": str(p), "status": "written"}

    task_id = uuid4()
    args = {"file_path": str(test_file), "content": content}
    args_hash = broker.compute_canonical_hash(args)

    auth = EffectAuthorization(
        task_id=task_id,
        user_id="user_admin",
        session_id=uuid4(),
        agent_id="coding_agent",
        tool_id="native:fs:write_file",
        canonical_arguments_hash=args_hash,
        target_resource=str(test_file),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="nonce_verified_test",
    )

    result = await broker.execute_step(
        task_id=auth.task_id,
        tool_id=manifest.capability_id,
        arguments=args,
        executor_fn=executor,
        authorization=auth,
        manifest=manifest,
    )

    assert result["status"] == "written"
    assert broker.last_receipt is not None
    assert broker.last_receipt.verified is True
    assert broker.last_receipt.capability_id == "native:fs:write_file"

    # Verify receipt was recorded in the persistent ledger
    stored_receipt = receipt_store.get(broker.last_receipt.receipt_id)
    assert stored_receipt is not None
    assert stored_receipt.verified is True


@pytest.mark.asyncio
async def test_action_broker_refutes_false_positive_execution(temp_dir: Path) -> None:
    """Verify ActionBroker fails-closed when tool claims success but external state check fails."""
    broker = ActionBroker()
    phantom_file = temp_dir / "phantom.txt"

    manifest = CapabilityManifest(
        capability_id="native:fs:write_file",
        owner="core",
        version="1.0.0",
        provider="builtin",
        tool_type=ToolType.NATIVE,
        description="Write file with verification requirement.",
        required_scopes=["file:write"],
        risk_class=RiskClass.BOUNDED_MUTATION,
        side_effect_class=SideEffectClass.IDEMPOTENT,
        allowed_trust_sources=[TrustLevel.USER_INPUT],
        sandbox_requirement=False,
        verification_requirement=True,
    )

    # Executor falsely claims file was written, but never touches disk
    async def false_positive_executor(args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "success", "file_path": args["file_path"]}

    task_id = uuid4()
    args = {"file_path": str(phantom_file), "content": "content"}
    args_hash = broker.compute_canonical_hash(args)

    auth = EffectAuthorization(
        task_id=task_id,
        user_id="user_admin",
        session_id=uuid4(),
        agent_id="coding_agent",
        tool_id="native:fs:write_file",
        canonical_arguments_hash=args_hash,
        target_resource=str(phantom_file),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="nonce_false_positive",
    )

    with pytest.raises(VerificationFailureError) as exc_info:
        await broker.execute_step(
            task_id=auth.task_id,
            tool_id=manifest.capability_id,
            arguments=args,
            executor_fn=false_positive_executor,
            authorization=auth,
            manifest=manifest,
        )

    assert "External verification refuted effect" in str(exc_info.value)
