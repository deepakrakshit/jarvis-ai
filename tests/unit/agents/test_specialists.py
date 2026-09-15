"""Unit tests for the 5 Capability Specialists and SpecialistRouter."""

import pytest

from jarvis.agents.analysis import AnalysisSpecialist
from jarvis.agents.base import SpecialistRole
from jarvis.agents.coding import CodingSpecialist
from jarvis.agents.computer import ComputerSpecialist
from jarvis.agents.personal import PersonalSpecialist
from jarvis.agents.research import ResearchSpecialist
from jarvis.agents.router import SpecialistRouter


@pytest.mark.asyncio
async def test_coding_specialist_proposals_and_clarifications() -> None:
    """Test CodingSpecialist file proposals and question generation."""
    specialist = CodingSpecialist()

    # 1. Actionable read proposal
    p1 = await specialist.propose("Please read file pyproject.toml")
    assert p1.tool_id == "native:fs:read_file"
    assert p1.arguments.get("file_path") == "pyproject.toml"
    assert p1.needs_clarification is False

    # 2. Ambiguous read request -> must ask clarifying question!
    p2 = await specialist.propose("Can you view file?")
    assert p2.needs_clarification is True
    assert p2.clarification_question is not None
    assert "Which file" in p2.clarification_question

    # 3. Directory listing proposal
    p3 = await specialist.propose("list files in jarvis")
    assert p3.tool_id == "native:fs:list_dir"
    assert p3.arguments.get("dir_path") == "jarvis"


@pytest.mark.asyncio
async def test_analysis_specialist_math_evaluation() -> None:
    """Test AnalysisSpecialist math calculation proposal."""
    specialist = AnalysisSpecialist()

    # Math formula
    p1 = await specialist.propose("calculate sqrt(144) * 5")
    assert p1.tool_id == "native:calc:evaluate"
    assert "sqrt(144)" in p1.arguments.get("expression", "")

    # Ambiguous data request -> asks question
    p2 = await specialist.propose("analyze the metrics please")
    assert p2.needs_clarification is True
    assert p2.clarification_question is not None


@pytest.mark.asyncio
async def test_computer_specialist_clock_and_os() -> None:
    """Test ComputerSpecialist time query proposal."""
    specialist = ComputerSpecialist()

    # Time query
    p1 = await specialist.propose("what is the current time and date?")
    assert p1.tool_id == "native:clock:get_time"

    # Dangerous shutdown -> asks confirmation
    p2 = await specialist.propose("reboot the system immediately")
    assert p2.needs_clarification is True
    assert p2.clarification_question is not None
    assert "confirmation" in p2.clarification_question.lower()


@pytest.mark.asyncio
async def test_personal_specialist_notes_management() -> None:
    """Test PersonalSpecialist note taking and recall."""
    specialist = PersonalSpecialist()

    # Save note
    p1 = await specialist.propose("remind me to check the deployment logs tomorrow")
    assert p1.direct_response is not None
    assert "check the deployment logs" in p1.direct_response

    # Retrieve notes
    p2 = await specialist.propose("show my notes")
    assert p2.direct_response is not None
    assert "check the deployment logs" in p2.direct_response

    # Empty reminder -> asks question
    p3 = await specialist.propose("remind me")
    assert p3.needs_clarification is True


@pytest.mark.asyncio
async def test_research_specialist_url_fetch() -> None:
    """Test ResearchSpecialist URL fetch proposal."""
    specialist = ResearchSpecialist()

    p1 = await specialist.propose("fetch https://example.com/docs")
    assert p1.tool_id == "native:web:fetch"
    assert p1.arguments.get("url") == "https://example.com/docs"

    # Vague search -> asks question
    p2 = await specialist.propose("search for")
    assert p2.needs_clarification is True


def test_specialist_router_intent_mapping() -> None:
    """Verify SpecialistRouter correctly selects the optimal specialist."""
    router = SpecialistRouter()

    assert router.route("calculate 25 * 4").role == SpecialistRole.ANALYSIS
    assert router.route("what time is it?").role == SpecialistRole.COMPUTER
    assert router.route("remind me to buy groceries").role == SpecialistRole.PERSONAL
    assert router.route("fetch https://python.org").role == SpecialistRole.RESEARCH
    assert router.route("read file README.md").role == SpecialistRole.CODING
