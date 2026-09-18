"""Unit & Contract Tests for JARVIS Governed Memory Plane.

Validates optimistic concurrency control (OCC), epistemic status calibration,
strict non-elevation invariants, supersession lineage, point-in-time temporal queries,
and native tool registration.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis.core.capabilities.builtin import register_builtin_capabilities
from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.exceptions import (
    MemoryConcurrencyConflictError,
    MemoryEpistemicViolationError,
)
from jarvis.core.memory.epistemic import EpistemicGovernor
from jarvis.core.memory.schemas import (
    EpistemicStatus,
    MemoryCategory,
    MemoryQuery,
    MemoryWriteProposal,
)
from jarvis.core.memory.storage import MemoryStore
from jarvis.core.trust.taxonomy import TrustLevel
from jarvis.tools.native import dispatch_native_tool


@pytest.fixture
def memory_db_path(tmp_path: Path) -> Path:
    """Provide an isolated temporary path for test SQLite database."""
    return tmp_path / "test_memory.db"


@pytest.fixture
def memory_store(memory_db_path: Path) -> MemoryStore:
    """Provide an initialized MemoryStore."""
    return MemoryStore(db_path=memory_db_path)


def test_epistemic_governor_calibration_rules() -> None:
    """Verify epistemic calibration enforces non-elevation from untrusted sources."""
    governor = EpistemicGovernor()

    # Untrusted source proposing VERIFIED_EXTERNAL or VERIFIED_INTERNAL must be rejected
    with pytest.raises(MemoryEpistemicViolationError):
        governor.calibrate_proposal(
            status=EpistemicStatus.VERIFIED_EXTERNAL,
            source_trust=TrustLevel.EXTERNAL_UNTRUSTED,
        )

    with pytest.raises(MemoryEpistemicViolationError):
        governor.calibrate_proposal(
            status=EpistemicStatus.VERIFIED_INTERNAL,
            source_trust=TrustLevel.EXTERNAL_UNTRUSTED,
        )

    # Untrusted source proposing PROPOSED is accepted as PROPOSED
    calibrated_untrusted = governor.calibrate_proposal(
        status=EpistemicStatus.PROPOSED,
        source_trust=TrustLevel.EXTERNAL_UNTRUSTED,
    )
    assert calibrated_untrusted == EpistemicStatus.PROPOSED

    # Direct user input proposing VERIFIED_EXTERNAL without witness is calibrated to OBSERVED
    user_calibrated = governor.calibrate_proposal(
        status=EpistemicStatus.VERIFIED_EXTERNAL,
        source_trust=TrustLevel.USER_INPUT,
        actor_id="agent",
        has_verification_witness=False,
    )
    assert user_calibrated == EpistemicStatus.OBSERVED

    # High trust verifier actor can commit VERIFIED_EXTERNAL
    verifier_calibrated = governor.calibrate_proposal(
        status=EpistemicStatus.VERIFIED_EXTERNAL,
        source_trust=TrustLevel.SYSTEM_POLICY,
        actor_id="verifier",
        has_verification_witness=True,
    )
    assert verifier_calibrated == EpistemicStatus.VERIFIED_EXTERNAL


def test_memory_store_initial_write_and_read(memory_store: MemoryStore) -> None:
    """Verify basic creation and retrieval of a governed memory record."""
    proposal = MemoryWriteProposal(
        tenant_id="tenant_a",
        user_id="user_1",
        category=MemoryCategory.USER_PREFERENCE,
        key="user_preference:theme",
        content="dark_mode",
        epistemic_status=EpistemicStatus.OBSERVED,
        source_trust_level=TrustLevel.USER_INPUT,
    )

    record = memory_store.write(proposal)
    assert record.memory_id
    assert record.version == 1
    assert record.key == "user_preference:theme"
    assert record.content == "dark_mode"
    assert record.superseded_by is None

    # Retrieve by ID
    fetched = memory_store.get(record.memory_id)
    assert fetched is not None
    assert fetched.memory_id == record.memory_id
    assert fetched.content == "dark_mode"
    assert fetched.category == MemoryCategory.USER_PREFERENCE


def test_memory_store_optimistic_concurrency_control(memory_store: MemoryStore) -> None:
    """Verify OCC version checks prevent lost updates and race conditions."""
    proposal_1 = MemoryWriteProposal(
        tenant_id="tenant_a",
        user_id="user_1",
        category=MemoryCategory.PROJECT_FACT,
        key="project_status",
        content="in_progress",
        expected_version=None,
    )
    rec_v1 = memory_store.write(proposal_1)
    assert rec_v1.version == 1

    # Successful update with correct expected_version
    proposal_2 = MemoryWriteProposal(
        tenant_id="tenant_a",
        user_id="user_1",
        category=MemoryCategory.PROJECT_FACT,
        key="project_status",
        content="review_ready",
        expected_version=1,
    )
    rec_v2 = memory_store.write(proposal_2)
    assert rec_v2.version == 2
    assert rec_v2.content == "review_ready"

    # Conflicting update with stale expected_version (1 instead of 2)
    proposal_conflict = MemoryWriteProposal(
        tenant_id="tenant_a",
        user_id="user_1",
        category=MemoryCategory.PROJECT_FACT,
        key="project_status",
        content="stale_overwrite",
        expected_version=1,
    )
    with pytest.raises(MemoryConcurrencyConflictError) as exc_info:
        memory_store.write(proposal_conflict)
    assert "Concurrency conflict" in str(exc_info.value)


def test_memory_store_supersession_history(memory_store: MemoryStore) -> None:
    """Verify supersession tracking maintains immutable audit lineage."""
    key = "deployment:config"

    # Revision 1
    v1 = memory_store.write(
        MemoryWriteProposal(
            tenant_id="t1",
            user_id="u1",
            category=MemoryCategory.SYSTEM_PROCEDURE,
            key=key,
            content="replica_count=1",
        )
    )

    # Revision 2
    v2 = memory_store.write(
        MemoryWriteProposal(
            tenant_id="t1",
            user_id="u1",
            category=MemoryCategory.SYSTEM_PROCEDURE,
            key=key,
            content="replica_count=3",
            expected_version=1,
        )
    )

    # Revision 3
    v3 = memory_store.write(
        MemoryWriteProposal(
            tenant_id="t1",
            user_id="u1",
            category=MemoryCategory.SYSTEM_PROCEDURE,
            key=key,
            content="replica_count=5",
            expected_version=2,
        )
    )

    # Verify supersession pointers
    fetched_v1 = memory_store.get(v1.memory_id)
    assert fetched_v1 is not None
    assert fetched_v1.superseded_by == v2.memory_id
    assert fetched_v1.valid_until is not None

    fetched_v2 = memory_store.get(v2.memory_id)
    assert fetched_v2 is not None
    assert fetched_v2.superseded_by == v3.memory_id

    fetched_v3 = memory_store.get(v3.memory_id)
    assert fetched_v3 is not None
    assert fetched_v3.superseded_by is None
    assert fetched_v3.valid_until is None

    # Full history retrieval
    history = memory_store.get_history(tenant_id="t1", user_id="u1", key=key)
    assert len(history) == 3
    assert [h.version for h in history] == [1, 2, 3]


def test_memory_store_temporal_as_of_query(memory_store: MemoryStore) -> None:
    """Verify point-in-time reconstruction with temporal queries."""
    key = "agent:state"

    t0 = datetime.now(UTC) - timedelta(seconds=10)
    v1 = memory_store.write(
        MemoryWriteProposal(
            tenant_id="t1",
            user_id="u1",
            category=MemoryCategory.EPISODIC_NOTE,
            key=key,
            content="state_idle",
            valid_from=t0 + timedelta(seconds=2),
        )
    )
    assert v1.version == 1

    t_between = t0 + timedelta(seconds=5)
    v2 = memory_store.write(
        MemoryWriteProposal(
            tenant_id="t1",
            user_id="u1",
            category=MemoryCategory.EPISODIC_NOTE,
            key=key,
            content="state_executing",
            expected_version=1,
            valid_from=t0 + timedelta(seconds=7),
        )
    )
    assert v2.version == 2

    # Query before v1 existed
    q_past = memory_store.query(MemoryQuery(tenant_id="t1", user_id="u1", key=key, as_of=t0))
    assert len(q_past) == 0

    # Query as of t_between should return v1
    q_mid = memory_store.query(MemoryQuery(tenant_id="t1", user_id="u1", key=key, as_of=t_between))
    assert len(q_mid) == 1
    assert q_mid[0].content == "state_idle"

    # Query current (latest) should return v2
    q_latest = memory_store.query(MemoryQuery(tenant_id="t1", user_id="u1", key=key))
    assert len(q_latest) == 1
    assert q_latest[0].content == "state_executing"


def test_memory_store_tombstone(memory_store: MemoryStore) -> None:
    """Verify tombstoning safely marks records as deleted under OCC."""
    key = "ephemeral:session_token"

    v1 = memory_store.write(
        MemoryWriteProposal(
            tenant_id="t1",
            user_id="u1",
            category=MemoryCategory.EPISODIC_NOTE,
            key=key,
            content="token_xyz_123",
        )
    )
    assert v1.version == 1

    # Tombstone with mismatched expected_version fails
    with pytest.raises(MemoryConcurrencyConflictError):
        memory_store.tombstone(
            tenant_id="t1",
            user_id="u1",
            key=key,
            expected_version=99,
        )

    # Successful tombstone
    ok = memory_store.tombstone(
        tenant_id="t1",
        user_id="u1",
        key=key,
        expected_version=1,
    )
    assert ok is True

    # Querying active records returns nothing
    active = memory_store.query(MemoryQuery(tenant_id="t1", user_id="u1", key=key))
    assert len(active) == 0


def test_memory_native_dispatcher_tools(memory_db_path: Path) -> None:
    """Verify dispatch_native_tool correctly handles native:memory:write and query."""
    import asyncio

    # Write memory record
    write_res = asyncio.run(
        dispatch_native_tool(
            "native:memory:write",
            {
                "category": "PROJECT_FACT",
                "key": "framework:preference",
                "content": "pytest",
                "tenant_id": "test_tenant",
                "user_id": "tester",
                "db_path": str(memory_db_path),
            },
        )
    )
    assert write_res["status"] == "committed"
    assert write_res["version"] == 1
    assert write_res["key"] == "framework:preference"

    # Query memory record
    query_res = asyncio.run(
        dispatch_native_tool(
            "native:memory:query",
            {
                "category": "PROJECT_FACT",
                "key": "framework:preference",
                "tenant_id": "test_tenant",
                "user_id": "tester",
                "db_path": str(memory_db_path),
            },
        )
    )
    assert query_res["count"] == 1
    assert query_res["records"][0]["content"] == "pytest"


def test_memory_builtin_capabilities_registration() -> None:
    """Verify memory capabilities register cleanly into CapabilityRegistry."""
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)

    write_manifest = registry.get("native:memory:write")
    assert write_manifest is not None
    assert write_manifest.required_scopes == ["memory:write"]

    query_manifest = registry.get("native:memory:query")
    assert query_manifest is not None
    assert query_manifest.required_scopes == ["memory:read"]
