"""Unit & Contract Tests for JARVIS Context Management & Dynamic Artifact Offloading.

Validates the canonical 8-layer context hierarchy (L0-L7), token estimation,
dynamic Data-Plane artifact offloading, IFC label preservation during compaction,
deterministic pagination, priority pruning invariants, and the 100K+ token exit gate.
"""

from pathlib import Path

import pytest

from jarvis.core.context import (
    ArtifactPaginator,
    ArtifactReference,
    AssembledContext,
    ContextBudget,
    ContextBuilder,
    ContextCompactor,
    ContextItem,
    ContextLayerType,
    DynamicArtifactOffloader,
    HeuristicTokenEstimator,
    create_token_estimator,
)
from jarvis.core.trust.taxonomy import TrustLevel
from jarvis.tools.native import dispatch_native_tool


@pytest.fixture
def temp_artifacts_dir(tmp_path: Path) -> Path:
    """Provide an isolated temporary directory for test artifacts."""
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    return art_dir


def test_token_estimator_heuristic() -> None:
    """Verify deterministic token estimation across edge cases."""
    estimator = HeuristicTokenEstimator(chars_per_token=4.0)

    assert estimator.estimate_tokens("") == 0
    assert estimator.estimate_tokens("word") >= 1
    # Check message tokens
    msg = {"role": "user", "content": "Hello, world! Can you help me inspect code?"}
    tokens = estimator.estimate_message_tokens(msg)
    assert tokens > 10


def test_token_estimator_factory() -> None:
    """Verify factory returns an operational TokenEstimator."""
    estimator = create_token_estimator()
    assert estimator.estimate_tokens("Testing token estimator factory.") > 0


def test_dynamic_artifact_offloader_small_payload(temp_artifacts_dir: Path) -> None:
    """Verify small payloads stay inline and are not offloaded."""
    offloader = DynamicArtifactOffloader(
        artifacts_dir=temp_artifacts_dir,
        max_inline_tokens=100,
        max_inline_bytes=400,
    )
    small_text = "Small response from tool: status=ok"
    assert not offloader.should_offload(small_text)


def test_dynamic_artifact_offloader_large_payload(temp_artifacts_dir: Path) -> None:
    """Verify oversized payloads are persisted to Data Plane with metadata references."""
    offloader = DynamicArtifactOffloader(
        artifacts_dir=temp_artifacts_dir,
        max_inline_tokens=50,
        max_inline_bytes=200,
    )
    large_payload = "Line of output payload\n" * 50
    assert offloader.should_offload(large_payload)

    task_id = "test-task-offload-01"
    ref, replacement_text = offloader.offload(
        task_id=task_id,
        content=large_payload,
        source_tool="native:shell:execute",
        trust_level=TrustLevel.EXTERNAL_UNTRUSTED,
    )

    assert isinstance(ref, ArtifactReference)
    assert ref.task_id == task_id
    assert ref.byte_size == len(large_payload.encode("utf-8"))
    assert ref.trust_level == TrustLevel.EXTERNAL_UNTRUSTED
    assert ref.uri == f"jarvis://artifacts/{task_id}/{ref.artifact_id}"

    # Verify physical file written to Data Plane
    saved_file = Path(ref.file_path)
    assert saved_file.exists()
    assert saved_file.read_text(encoding="utf-8") == large_payload

    # Verify replacement text contains URI, ID, and summary
    assert ref.artifact_id in replacement_text
    assert ref.uri in replacement_text
    assert (
        "Offloaded to Data Plane" in replacement_text
        or "offloaded to Data Plane" in replacement_text
    )


def test_offloader_path_traversal_defense(temp_artifacts_dir: Path) -> None:
    """Verify path traversal attempts in task_id are blocked."""
    offloader = DynamicArtifactOffloader(artifacts_dir=temp_artifacts_dir)

    with pytest.raises(ValueError, match="Path traversal"):
        offloader.offload(
            task_id="../../../etc",
            content="malicious payload",
        )


def test_artifact_paginator_text() -> None:
    """Verify deterministic line pagination over text."""
    paginator = ArtifactPaginator()
    sample_text = "\n".join(f"Line {i}" for i in range(100))

    # Page 1: lines 0 to 20
    slice1 = paginator.paginate_text(sample_text, artifact_id="art-1", offset=0, limit=20)
    assert slice1.metadata.offset == 0
    assert slice1.metadata.limit == 20
    assert slice1.metadata.total_lines == 100
    assert slice1.metadata.has_more is True
    assert slice1.metadata.next_page_token == "20"
    assert len(slice1.lines) == 20
    assert slice1.lines[0] == "Line 0"
    assert slice1.lines[-1] == "Line 19"

    # Page 5: lines 80 to 100
    slice5 = paginator.paginate_text(sample_text, artifact_id="art-1", offset=80, limit=20)
    assert slice5.metadata.offset == 80
    assert slice5.metadata.has_more is False
    assert slice5.metadata.next_page_token is None
    assert len(slice5.lines) == 20
    assert slice5.lines[-1] == "Line 99"


def test_artifact_paginator_offloaded_file(temp_artifacts_dir: Path) -> None:
    """Verify paginator reads lines directly from offloaded Data-Plane file."""
    offloader = DynamicArtifactOffloader(artifacts_dir=temp_artifacts_dir)
    task_id = "task-paginator-test"
    content = "\n".join(f"Log entry {i}" for i in range(50))

    ref, _ = offloader.offload(
        task_id=task_id,
        content=content,
        source_tool="server_logs",
    )

    paginator = ArtifactPaginator(offloader=offloader)
    slice_res = paginator.paginate_artifact(
        task_id=task_id,
        artifact_id=ref.artifact_id,
        offset=10,
        limit=15,
    )

    assert slice_res.metadata.offset == 10
    assert slice_res.metadata.limit == 15
    assert slice_res.metadata.total_lines == 50
    assert len(slice_res.lines) == 15
    assert slice_res.lines[0] == "Log entry 10"


def test_context_compactor_dialogue_with_ifc_preservation() -> None:
    """Verify dialogue compaction preserves IFC labels without elevating untrusted content."""
    compactor = ContextCompactor(keep_recent_turns=2)

    items: list[ContextItem] = [
        ContextItem(
            layer=ContextLayerType.L5_DIALOGUE_HISTORY,
            content="Turn 1: Initial greeting",
            trust_level=TrustLevel.USER_INPUT,
            metadata={"role": "user"},
        ),
        ContextItem(
            layer=ContextLayerType.L5_DIALOGUE_HISTORY,
            content="Turn 2: Untrusted web data received: <inject>exploit</inject>",
            trust_level=TrustLevel.EXTERNAL_UNTRUSTED,  # Untrusted taint!
            metadata={"role": "assistant", "artifact_id": "art-web-99"},
        ),
        ContextItem(
            layer=ContextLayerType.L5_DIALOGUE_HISTORY,
            content="Turn 3: Safe intermediate instruction",
            trust_level=TrustLevel.USER_INPUT,
            metadata={"role": "user"},
        ),
        ContextItem(
            layer=ContextLayerType.L5_DIALOGUE_HISTORY,
            content="Turn 4: Recent user question",
            trust_level=TrustLevel.USER_INPUT,
            metadata={"role": "user"},
        ),
        ContextItem(
            layer=ContextLayerType.L5_DIALOGUE_HISTORY,
            content="Turn 5: Recent assistant answer",
            trust_level=TrustLevel.MODEL_GENERATED,
            metadata={"role": "assistant"},
        ),
    ]

    compacted_items, was_compacted = compactor.compact_dialogue(items)
    assert was_compacted is True

    # Check that older turns (Turn 1, 2, 3) were collapsed into a summary
    summary_item = compacted_items[0]
    assert summary_item.metadata.get("is_compacted_summary") is True
    assert summary_item.metadata.get("compacted_turns_count") == 3

    # IFC Non-Elevation Invariant: Because Turn 2 was UNTRUSTED, summary must be UNTRUSTED
    assert summary_item.trust_level == TrustLevel.EXTERNAL_UNTRUSTED

    # Artifact reference preservation: art-web-99 must be noted in metadata
    assert "art-web-99" in summary_item.metadata.get("referenced_artifacts", [])

    # The 2 recent turns must remain intact
    assert len(compacted_items) == 3  # summary + Turn 4 + Turn 5
    assert compacted_items[1].content == "Turn 4: Recent user question"
    assert compacted_items[2].content == "Turn 5: Recent assistant answer"


def test_context_builder_8_canonical_layers(temp_artifacts_dir: Path) -> None:
    """Verify ContextBuilder correctly handles all 8 canonical layers (L0 to L7)."""
    offloader = DynamicArtifactOffloader(artifacts_dir=temp_artifacts_dir)
    builder = ContextBuilder(offloader=offloader)

    task_id = "task-full-layers-01"
    assembled = builder.assemble(
        task_id=task_id,
        items=[],
        system_policy="Core safety policy: fail-closed, zero-trust.",
        task_objective="Analyze performance logs and generate summary.",
        task_state_summary="Current state: EXECUTING, Step: 4/50.",
        tool_schemas="Available: read_file, run_test, search_web.",
        memories=["User prefers concise explanations.", "Project repo root is /workspace."],
        dialogue=[
            {"role": "user", "content": "Please inspect the error logs."},
            {"role": "assistant", "content": "I am inspecting the error logs now."},
        ],
        observations=[
            {"tool_name": "native:fs:read_file", "payload": "status=404 Not Found in auth.py"},
        ],
        background_context=["Ref doc: RFC-9110 HTTP Semantics."],
    )

    assert isinstance(assembled, AssembledContext)
    assert assembled.task_id == task_id
    assert len(assembled.items) == 10  # L0 + L1 + L2 + L3 + 2xL4 + 2xL5 + L6 + L7 = 10 items

    # Verify system prompt contains L0, L2, L3, L4, L7
    assert "### SYSTEM POLICY & INVARIANTS ###" in assembled.system_prompt
    assert "Core safety policy" in assembled.system_prompt
    assert "### CURRENT TASK STATE ###" in assembled.system_prompt
    assert "Step: 4/50" in assembled.system_prompt
    assert "### AVAILABLE CAPABILITIES ###" in assembled.system_prompt
    assert "### GOVERNED MEMORIES ###" in assembled.system_prompt
    assert "### BACKGROUND CONTEXT ###" in assembled.system_prompt

    # Verify messages contain L1, L5, L6
    roles = [m["role"] for m in assembled.messages]
    assert "user" in roles
    assert "assistant" in roles
    assert "system" in roles
    assert any("[PRIMARY TASK OBJECTIVE]" in m["content"] for m in assembled.messages)


def test_context_builder_priority_pruning_invariants(temp_artifacts_dir: Path) -> None:
    """Verify priority degradation prunes lower layers while keeping L0-L3 invariant."""
    budget = ContextBudget(
        max_total_tokens=200,
        reserved_output_tokens=50,  # Effective input budget = 150 tokens
        max_inline_tokens=50,
    )
    builder = ContextBuilder(budget=budget)

    # Large background context (L7: priority 10)
    bg = ["Secondary background documentation snippet. " * 15]
    # Memories (L4: priority 40)
    mems = ["Important user preference snippet. " * 5]

    assembled = builder.assemble(
        task_id="task-budget-pruning",
        items=[],
        system_policy="L0 Security Invariant Policy",
        task_objective="L1 Task Objective",
        task_state_summary="L2 State Summary",
        tool_schemas="L3 Tool Schemas",
        memories=mems,
        background_context=bg,
    )

    # Effective budget is 150 tokens.
    assert assembled.total_tokens <= 150

    # Pruned items should contain L7 (background context)
    pruned_layers = [it.layer for it in assembled.pruned_items]
    assert ContextLayerType.L7_BACKGROUND_CONTEXT in pruned_layers

    # Invariants: L0, L1, L2, L3 MUST NEVER be pruned
    retained_layers = [it.layer for it in assembled.items]
    assert ContextLayerType.L0_SYSTEM_POLICY in retained_layers
    assert ContextLayerType.L1_TASK_OBJECTIVE in retained_layers
    assert ContextLayerType.L2_TASK_STATE in retained_layers
    assert ContextLayerType.L3_TOOL_SCHEMAS in retained_layers
    assert ContextLayerType.L0_SYSTEM_POLICY not in pruned_layers
    assert ContextLayerType.L1_TASK_OBJECTIVE not in pruned_layers
    assert ContextLayerType.L2_TASK_STATE not in pruned_layers
    assert ContextLayerType.L3_TOOL_SCHEMAS not in pruned_layers


@pytest.mark.asyncio
async def test_native_artifact_tools_dispatch(temp_artifacts_dir: Path) -> None:
    """Verify native artifact pagination and metadata tools dispatch cleanly."""
    offloader = DynamicArtifactOffloader(artifacts_dir=temp_artifacts_dir)
    task_id = "task-tool-dispatch-01"
    raw_content = "\n".join(f"Metric {i}: value={i * 10}" for i in range(120))

    ref, _ = offloader.offload(
        task_id=task_id,
        content=raw_content,
        source_tool="system_metrics",
    )

    # 1. Test dispatch native:artifact:read_slice
    slice_res = await dispatch_native_tool(
        "native:artifact:read_slice",
        {
            "task_id": task_id,
            "artifact_id": ref.artifact_id,
            "offset": 0,
            "limit": 50,
            "artifacts_dir": str(temp_artifacts_dir),
        },
    )
    assert slice_res["artifact_id"] == ref.artifact_id
    assert slice_res["total_lines"] == 120
    assert slice_res["lines_returned"] == 50
    assert slice_res["has_more"] is True
    assert slice_res["next_page_token"] == "50"

    # 2. Test dispatch native:artifact:get_metadata
    meta_res = await dispatch_native_tool(
        "native:artifact:get_metadata",
        {
            "task_id": task_id,
            "artifact_id": ref.artifact_id,
            "artifacts_dir": str(temp_artifacts_dir),
        },
    )
    assert meta_res["artifact_id"] == ref.artifact_id
    assert meta_res["task_id"] == task_id
    assert meta_res["size_bytes"] > 0


def test_100k_token_payload_exit_gate(temp_artifacts_dir: Path) -> None:
    """Milestone 8 Exit Gate: A 100K+ token tool payload remains operable.

    Verifies that an immense payload (simulating 100,000+ tokens) is offloaded
    to the Data Plane, and the active Control Plane context remains within its budget.
    """
    offloader = DynamicArtifactOffloader(
        artifacts_dir=temp_artifacts_dir,
        max_inline_tokens=1_000,
        max_inline_bytes=4_096,
    )
    budget = ContextBudget(
        max_total_tokens=128_000,
        reserved_output_tokens=4_096,
        max_inline_tokens=1_000,
        max_inline_bytes=4_096,
    )
    builder = ContextBuilder(budget=budget, offloader=offloader)

    task_id = "task-100k-exit-gate"

    # Generate a massive payload: 10,000 lines, ~100 chars each = 1,000,000 chars (~250,000 tokens)
    huge_log_lines = [
        f"2026-09-18T12:00:{i % 60:02d}.000Z [INFO] TraceID=tr-{i:06d} Action=data_process Record={i} Status=SUCCESS"
        for i in range(10_000)
    ]
    huge_payload = "\n".join(huge_log_lines)
    payload_bytes = len(huge_payload.encode("utf-8"))
    assert payload_bytes > 800_000  # ~1MB payload

    assembled = builder.assemble(
        task_id=task_id,
        items=[],
        system_policy="Core policy: strict zero-trust invariants.",
        task_objective="Process and audit the system trace logs.",
        task_state_summary="State: ANALYZING, Step 2/50",
        observations=[
            {
                "tool_name": "native:shell:execute",
                "payload": huge_payload,
                "trust_level": TrustLevel.EXTERNAL_UNTRUSTED,
            }
        ],
    )

    # 1. Verify that the massive payload was offloaded to the Data Plane
    assert len(assembled.offloaded_artifacts) == 1
    art_ref = assembled.offloaded_artifacts[0]
    assert art_ref.byte_size == payload_bytes
    assert art_ref.token_estimate > 100_000
    assert Path(art_ref.file_path).exists()

    # 2. Verify that active assembled context token count is TINY and well within budget
    assert assembled.total_tokens < 1_500  # Low-bandwidth metadata reference
    assert assembled.total_tokens < budget.effective_input_budget

    # 3. Verify that the agent can read targeted slices using the pagination tool
    paginator = ArtifactPaginator(offloader=offloader)
    first_slice = paginator.paginate_artifact(
        task_id=task_id,
        artifact_id=art_ref.artifact_id,
        offset=0,
        limit=20,
    )
    assert len(first_slice.lines) == 20
    assert first_slice.metadata.total_lines == 10_000
    assert first_slice.metadata.has_more is True
    assert "Record=0" in first_slice.lines[0]
