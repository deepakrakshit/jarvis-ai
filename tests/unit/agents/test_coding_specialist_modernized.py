"""Unit tests for the modernized Coding Specialist with sandbox execution capabilities."""

from __future__ import annotations

import pytest

from jarvis.agents.base import SpecialistProposal, SpecialistRole
from jarvis.agents.coding import CodingSpecialist


@pytest.mark.asyncio
async def test_coding_specialist_manifest_scopes() -> None:
    """Verify Coding Specialist manifest includes both sandbox and host test scopes."""
    specialist = CodingSpecialist()
    scopes = specialist.manifest.allowed_tool_scopes
    assert "sandbox:code:execute" in scopes
    assert "native:code:run_test" in scopes
    assert "native:fs:read_file" in scopes
    assert "native:fs:write_file" in scopes


@pytest.mark.asyncio
async def test_coding_specialist_propose_sandbox_execution() -> None:
    """Verify Coding Specialist proposes sandbox:code:execute when sandbox is requested."""
    specialist = CodingSpecialist()

    proposal = await specialist.propose("Please run pytest in sandbox")
    assert proposal.specialist_role == SpecialistRole.CODING
    assert proposal.tool_id == "sandbox:code:execute"
    assert "command" in proposal.arguments
    assert "pytest" in proposal.arguments["command"]

    proposal2 = await specialist.propose("Run this script in isolated execution")
    assert proposal2.tool_id == "sandbox:code:execute"


@pytest.mark.asyncio
async def test_coding_specialist_synthesize_sandbox_result() -> None:
    """Verify Coding Specialist records sandboxed execution results into its scratchpad."""
    specialist = CodingSpecialist()
    proposal = SpecialistProposal(
        specialist_role=SpecialistRole.CODING,
        intent="Run test in sandbox",
        tool_id="sandbox:code:execute",
        arguments={"command": ["pytest"]},
    )
    tool_result = {
        "exit_code": 0,
        "stdout": "1 passed in 0.05s",
        "stderr": "",
        "duration_seconds": 0.05,
        "timed_out": False,
        "sandbox_id": "test-sbx-1234",
        "isolation_tier": "TIER_1_CONTAINER",
        "provider_category": "REAL_ISOLATION_PROVIDER",
        "sandbox_status": "SANDBOX_ACTIVE",
    }

    synth = await specialist.synthesize(
        proposal=proposal,
        tool_result=tool_result,
        user_message="Run test in sandbox",
    )
    assert synth is not None

    # Check scratchpad notes
    notes = specialist.scratchpad.notes
    assert any("Sandboxed execution in TIER_1_CONTAINER" in n for n in notes)
