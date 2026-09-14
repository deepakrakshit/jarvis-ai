"""Tests for Stage 6 Specialist Stubs and Manifest Declarations."""

import pytest

from jarvis.agents.analysis import AnalysisSpecialist
from jarvis.agents.coding import CodingSpecialist
from jarvis.agents.computer import ComputerSpecialist
from jarvis.agents.personal import PersonalSpecialist
from jarvis.agents.research import ResearchSpecialist


def test_specialist_manifest_declarations() -> None:
    """Verify that each specialist stub registers correct metadata and scopes."""
    research = ResearchSpecialist()
    assert research.manifest.name == "research"
    assert "web.search" in research.manifest.allowed_tool_scopes
    assert research.manifest.memory_mode == "PER_SPECIALIST"

    coding = CodingSpecialist()
    assert coding.manifest.name == "coding"
    assert "fs.read" in coding.manifest.allowed_tool_scopes
    assert "code.ast" in coding.manifest.allowed_tool_scopes

    computer = ComputerSpecialist()
    assert computer.manifest.name == "computer"
    assert "os.window" in computer.manifest.allowed_tool_scopes

    personal = PersonalSpecialist()
    assert personal.manifest.name == "personal"
    assert "calendar.read" in personal.manifest.allowed_tool_scopes
    assert personal.manifest.memory_mode == "SHARED"

    analysis = AnalysisSpecialist()
    assert analysis.manifest.name == "analysis"
    assert "data.parse" in analysis.manifest.allowed_tool_scopes


@pytest.mark.asyncio
async def test_specialist_stubs_raise_not_implemented() -> None:
    """Verify that invoking stubs in Stage 0 cleanly raises NotImplementedError."""
    specialists = [
        ResearchSpecialist(),
        CodingSpecialist(),
        ComputerSpecialist(),
        PersonalSpecialist(),
        AnalysisSpecialist(),
    ]
    for s in specialists:
        with pytest.raises(NotImplementedError):
            await s.process_task({"test": 123})
