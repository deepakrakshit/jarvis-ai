"""Unit tests for the 5-step HITL approval pipeline and commit-time authorization."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from jarvis.core.exceptions import PolicyViolationError
from jarvis.core.policy.hitl import (
    ApprovalRequest,
    ApprovalStatus,
    HITLPipeline,
    compute_canonical_arguments_hash,
)


def _assert_approval_status(req: ApprovalRequest, expected: ApprovalStatus) -> None:
    assert req.status == expected


def test_canonical_arguments_hash_deterministic() -> None:
    """Verify arguments hashing is deterministic regardless of key insertion order."""
    args_1 = {"z_param": 10, "a_param": "test", "inner": {"k2": 2, "k1": 1}}
    args_2 = {"a_param": "test", "z_param": 10, "inner": {"k1": 1, "k2": 2}}

    hash_1 = compute_canonical_arguments_hash(args_1)
    hash_2 = compute_canonical_arguments_hash(args_2)

    assert hash_1 == hash_2
    assert len(hash_1) == 64  # SHA-256


def test_create_and_resolve_approval_request() -> None:
    """Verify creating an approval request and approving it issues EffectAuthorization."""
    pipeline = HITLPipeline(default_ttl_seconds=300)
    task_id = uuid4()
    args = {"file_path": "/workspace/doc.txt", "content": "approved content"}

    req = pipeline.create_approval_request(
        task_id=task_id,
        tool_id="native:fs:write_file",
        arguments=args,
        target_resource="/workspace/doc.txt",
        risk_score=0.45,
    )

    _assert_approval_status(req, ApprovalStatus.PENDING)
    assert req.canonical_arguments_hash == compute_canonical_arguments_hash(args)

    # Approve request
    auth = pipeline.resolve_approval(req.request_id, approved=True, user_id="human_operator")
    assert auth is not None
    assert auth.task_id == task_id
    assert auth.tool_id == "native:fs:write_file"
    assert auth.user_id == "human_operator"
    assert auth.is_valid() is True
    _assert_approval_status(req, ApprovalStatus.APPROVED)


def test_reject_approval_request() -> None:
    """Verify rejecting an approval request leaves authorization as None."""
    pipeline = HITLPipeline()
    req = pipeline.create_approval_request(
        task_id=uuid4(),
        tool_id="native:fs:write_file",
        arguments={"file": "foo"},
        target_resource="foo",
        risk_score=0.80,
    )

    auth = pipeline.resolve_approval(req.request_id, approved=False, user_id="admin")
    assert auth is None
    assert req.status == ApprovalStatus.REJECTED


def test_expired_approval_request_fails_closed() -> None:
    """Verify attempting to resolve an expired approval request is blocked."""
    pipeline = HITLPipeline(default_ttl_seconds=1)
    req = pipeline.create_approval_request(
        task_id=uuid4(),
        tool_id="native:shell:execute",
        arguments={"command": "shutdown"},
        target_resource="os",
        risk_score=0.99,
    )

    # Simulate TTL expiration
    req.expires_at = datetime.now(UTC) - timedelta(seconds=10)

    with pytest.raises(PolicyViolationError, match="expired"):
        pipeline.resolve_approval(req.request_id, approved=True)


def test_revalidate_and_commit_happy_path() -> None:
    """Verify post-approval revalidation and single-use consumption succeed on matching payload."""
    pipeline = HITLPipeline()
    args = {"command": "echo 'safe'"}
    req = pipeline.create_approval_request(
        task_id=uuid4(),
        tool_id="native:shell:execute",
        arguments=args,
        target_resource="shell",
        risk_score=0.50,
    )
    auth = pipeline.resolve_approval(req.request_id, approved=True)
    assert auth is not None

    # Commit with identical arguments
    pipeline.revalidate_and_commit(auth, actual_arguments=args)

    # Token is now consumed; reuse must fail
    assert auth.used is True
    with pytest.raises(PolicyViolationError, match="invalid or expired"):
        pipeline.revalidate_and_commit(auth, actual_arguments=args)


def test_revalidate_toctou_parameter_tampering_rejected() -> None:
    """Verify TOCTOU defense: modifying arguments between approval and execution is rejected."""
    pipeline = HITLPipeline()
    approved_args = {"command": "echo 'harmless'"}
    tampered_args = {"command": "rm -rf /"}

    req = pipeline.create_approval_request(
        task_id=uuid4(),
        tool_id="native:shell:execute",
        arguments=approved_args,
        target_resource="shell",
        risk_score=0.50,
    )
    auth = pipeline.resolve_approval(req.request_id, approved=True)
    assert auth is not None

    # Attempt to commit with tampered arguments
    with pytest.raises(PolicyViolationError, match="TOCTOU violation"):
        pipeline.revalidate_and_commit(auth, actual_arguments=tampered_args)


def test_revalidate_target_witness_mismatch_rejected() -> None:
    """Verify target witness hash mismatch triggers state drift abort."""
    pipeline = HITLPipeline()
    args = {"file": "state.json"}
    req = pipeline.create_approval_request(
        task_id=uuid4(),
        tool_id="native:fs:write_file",
        arguments=args,
        target_resource="state.json",
        risk_score=0.40,
    )
    auth = pipeline.resolve_approval(req.request_id, approved=True)
    assert auth is not None

    # Bind witness
    auth.target_witness_hash = "etag_v1_hash"

    # Attempt commit with changed witness state
    with pytest.raises(PolicyViolationError, match="Target witness mismatch"):
        pipeline.revalidate_and_commit(
            auth, actual_arguments=args, current_witness_hash="etag_v2_modified"
        )


def test_find_active_authorization() -> None:
    """Verify finding active authorizations by tool_id and canonical arguments hash."""
    pipeline = HITLPipeline()
    args = {"file_path": "test.py", "mode": "script"}
    args_hash = compute_canonical_arguments_hash(args)

    # 1. Initially none found
    assert pipeline.find_active_authorization("native:code:run_test", args_hash) is None

    # 2. Create and approve request
    req = pipeline.create_approval_request(
        task_id=uuid4(),
        tool_id="native:code:run_test",
        arguments=args,
        target_resource="test.py",
        risk_score=0.7,
    )
    auth = pipeline.resolve_approval(req.request_id, approved=True)
    assert auth is not None

    # 3. Lookup finds valid active authorization
    found = pipeline.find_active_authorization("native:code:run_test", args_hash)
    assert found is not None
    assert found.proposal_id == auth.proposal_id

    # 4. Lookup with different tool_id returns None
    assert pipeline.find_active_authorization("native:shell:execute", args_hash) is None

    # 5. Lookup with different arguments hash returns None
    other_hash = compute_canonical_arguments_hash({"file_path": "other.py"})
    assert pipeline.find_active_authorization("native:code:run_test", other_hash) is None

    # 6. Once consumed, lookup returns None
    auth.mark_used()
    assert pipeline.find_active_authorization("native:code:run_test", args_hash) is None


def test_find_latest_pending_request_and_resolve_latest() -> None:
    """Verify discovering and resolving the most recent pending approval request."""
    pipeline = HITLPipeline()
    args_1 = {"cmd": "first"}
    args_2 = {"cmd": "second"}

    req_1 = pipeline.create_approval_request(
        task_id=uuid4(),
        tool_id="native:shell:execute",
        arguments=args_1,
        target_resource="first",
        risk_score=0.8,
    )
    req_2 = pipeline.create_approval_request(
        task_id=uuid4(),
        tool_id="native:shell:execute",
        arguments=args_2,
        target_resource="second",
        risk_score=0.8,
    )

    # Latest pending is req_2
    latest = pipeline.find_latest_pending_request()
    assert latest is not None
    assert latest.request_id == req_2.request_id

    # Resolve latest
    auth = pipeline.resolve_latest_pending(approved=True, user_id="operator")
    assert auth is not None
    assert auth.proposal_id == req_2.request_id
    assert req_2.status == ApprovalStatus.APPROVED

    # Now latest pending is req_1
    latest_remaining = pipeline.find_latest_pending_request()
    assert latest_remaining is not None
    assert latest_remaining.request_id == req_1.request_id
