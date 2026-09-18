"""JARVIS Native Governed Memory Capabilities.

Provides governed native tools for querying and proposing writes to the Memory Plane.
"""

from datetime import datetime
from typing import Any

from jarvis.core.memory.schemas import (
    EpistemicStatus,
    MemoryCategory,
    MemoryQuery,
    MemoryWriteProposal,
)
from jarvis.core.memory.storage import MemoryStore
from jarvis.core.trust.taxonomy import TrustLevel


def write_memory(
    category: str,
    key: str,
    content: str,
    expected_version: int | None = None,
    tenant_id: str = "default",
    user_id: str = "default_user",
    epistemic_status: str = "PROPOSED",
    source_trust_level: str = "EXTERNAL_UNTRUSTED",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Write or revise a memory record under optimistic concurrency control."""
    store = MemoryStore(db_path=db_path)
    mem_cat = MemoryCategory(category)
    epi_stat = EpistemicStatus(epistemic_status)
    trust_lvl = TrustLevel(source_trust_level)

    proposal = MemoryWriteProposal(
        tenant_id=tenant_id,
        user_id=user_id,
        category=mem_cat,
        key=key,
        content=content,
        epistemic_status=epi_stat,
        source_trust_level=trust_lvl,
        expected_version=expected_version,
    )

    rec = store.write(proposal)
    return {
        "memory_id": rec.memory_id,
        "key": rec.key,
        "version": rec.version,
        "epistemic_status": rec.epistemic_status.value,
        "status": "committed",
    }


def query_memory(
    category: str | None = None,
    key: str | None = None,
    as_of: str | None = None,
    tenant_id: str = "default",
    user_id: str = "default_user",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Query memory records with categorical and point-in-time filtering."""
    store = MemoryStore(db_path=db_path)
    mem_cat = MemoryCategory(category) if category else None
    as_of_dt = datetime.fromisoformat(as_of) if as_of else None

    query = MemoryQuery(
        tenant_id=tenant_id,
        user_id=user_id,
        category=mem_cat,
        key=key,
        as_of=as_of_dt,
    )

    records = store.query(query)
    return {
        "count": len(records),
        "records": [
            {
                "memory_id": r.memory_id,
                "category": r.category.value,
                "key": r.key,
                "content": r.content,
                "version": r.version,
                "epistemic_status": r.epistemic_status.value,
                "valid_from": r.valid_from.isoformat(),
                "valid_until": r.valid_until.isoformat() if r.valid_until else None,
            }
            for r in records
        ],
    }
