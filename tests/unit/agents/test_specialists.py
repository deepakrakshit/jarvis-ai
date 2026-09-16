"""Unit tests for the 5 Capability Specialists and SpecialistRouter."""

from pathlib import Path

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


def test_specialist_scratchpad_isolation_contract_04() -> None:
    """Verify Contract 04 scratchpad isolation and methods prevent context contamination."""
    from jarvis.agents.base import SpecialistScratchpad

    s1 = SpecialistScratchpad(specialist_role=SpecialistRole.CODING)
    s2 = SpecialistScratchpad(specialist_role=SpecialistRole.RESEARCH)

    s1.add_note("Analyzing AST of parser.py")
    s1.add_artifact("jarvis/parser.py")

    s2.add_note("Querying Google search API")
    s2.add_evidence("https://example.com", "Factual citation content", confidence=0.9)

    # Scratchpads must be strictly isolated
    assert len(s1.notes) == 1
    assert len(s1.artifacts_touched) == 1
    assert len(s1.evidence) == 0

    assert len(s2.notes) == 1
    assert len(s2.evidence) == 1
    assert len(s2.artifacts_touched) == 0

    assert "parser.py" in s1.export_summary()
    assert "Evidence items: 1" in s2.export_summary()

    s1.clear()
    assert len(s1.notes) == 0
    assert len(s1.artifacts_touched) == 0
    assert s1.export_summary() == "Scratchpad empty"


def test_specialist_result_schema_contract_04() -> None:
    """Verify SpecialistResult data contract structure."""
    from jarvis.agents.base import SpecialistResult

    res = SpecialistResult(
        specialist_role=SpecialistRole.CODING,
        intent="Read configuration file",
        success=True,
        response="File contents retrieved",
        tool_executed="native:fs:read_file",
        artifacts_touched=["pyproject.toml"],
        duration_ms=4.2,
    )
    assert res.success is True
    assert res.specialist_role == SpecialistRole.CODING
    assert res.artifacts_touched == ["pyproject.toml"]
    assert res.duration_ms == 4.2


@pytest.mark.asyncio
async def test_computer_specialist_system_stats_and_probe() -> None:
    """Verify ComputerSpecialist handles system metrics and health probe."""
    computer = ComputerSpecialist()

    # Proposal for system stats
    prop = await computer.propose("show system stats and cpu usage")
    assert prop.tool_id == "native:system:get_stats"

    # Synthesis populates scratchpad
    synth = await computer.synthesize(
        prop,
        {
            "platform": "Windows",
            "platform_release": "11",
            "cpu_percent": 12.5,
            "cpu_count": 8,
            "memory_percent": 65.0,
            "memory_used_gb": 10.4,
            "memory_total_gb": 16.0,
            "disk_percent": 45.0,
            "uptime_seconds": 7200,
        },
        "show system stats",
    )
    assert "CPU" in synth
    assert "Memory" in synth
    assert len(computer.get_scratchpad().notes) > 0

    # Health probe
    probe = await computer.health_probe()
    assert probe.healthy is True
    assert probe.component_id == "specialist:computer"


@pytest.mark.asyncio
async def test_analysis_specialist_statistics_and_probe() -> None:
    """Verify AnalysisSpecialist computes summary statistics and health probe."""
    analysis = AnalysisSpecialist()

    # Statistical summary proposal
    prop = await analysis.propose("statistics of 10, 20, 30, 40, 50")
    assert prop.direct_response is not None
    assert "Mean" in prop.direct_response
    assert "30.0" in prop.direct_response

    # Math evaluation synthesis populates scratchpad
    calc_prop = await analysis.propose("calculate 15 * 3")
    assert calc_prop.tool_id == "native:calc:evaluate"
    synth = await analysis.synthesize(
        calc_prop,
        {"expression": "15 * 3", "result": 45},
        "calculate 15 * 3",
    )
    assert "45" in synth
    assert len(analysis.get_scratchpad().notes) > 0

    # Health probe executes self-test (2 + 2 == 4)
    probe = await analysis.health_probe()
    assert probe.healthy is True
    assert probe.component_id == "specialist:analysis"


@pytest.mark.asyncio
async def test_personal_specialist_categorization_and_repair() -> None:
    """Verify PersonalSpecialist categorizes notes and implements repair."""
    from jarvis.agents.personal import NoteCategory

    personal = PersonalSpecialist()

    # 1. Reminder
    await personal.propose("remind me to commit my changes")
    assert len(personal.notes) == 1
    assert personal.notes[0].category == NoteCategory.REMINDER

    # 2. Preference
    await personal.propose("remember that I prefer dark theme")
    assert len(personal.notes) == 2
    assert personal.notes[1].category == NoteCategory.PREFERENCE

    # 3. Filtered retrieval
    pref_prop = await personal.propose("show my preferences")
    assert "prefer dark theme" in str(pref_prop.direct_response)

    # 4. Health probe and repair
    probe = await personal.health_probe()
    assert probe.healthy is True
    assert probe.details["notes_count"] == 2

    repaired = await personal.repair()
    assert repaired is True


@pytest.mark.asyncio
async def test_coding_and_research_synthesis_scratchpad_tracking() -> None:
    """Verify ResearchSpecialist and CodingSpecialist record evidence and artifacts in scratchpad."""
    # Research
    research = ResearchSpecialist()
    search_prop = await research.propose("search for rust async")
    await research.synthesize(
        search_prop,
        {
            "query": "rust async",
            "results": [
                {
                    "title": "Rust Async Book",
                    "url": "https://rust-lang.org/async",
                    "snippet": "Async book snippet",
                }
            ],
        },
        "search for rust async",
    )
    assert len(research.get_scratchpad().evidence) == 1
    assert research.get_scratchpad().evidence[0]["source"] == "https://rust-lang.org/async"

    # Coding
    coding = CodingSpecialist()
    read_prop = await coding.propose("read file jarvis/orchestrator.py")
    await coding.synthesize(
        read_prop,
        {"file_path": "jarvis/orchestrator.py", "content": "import sys", "size_bytes": 10},
        "read file jarvis/orchestrator.py",
    )
    assert "jarvis/orchestrator.py" in coding.get_scratchpad().artifacts_touched


@pytest.mark.asyncio
async def test_orchestrator_specialist_lifecycle_quarantine_and_self_healing(
    tmp_path: Path,
) -> None:
    """Verify orchestrator registers specialists in lifecycle, handles quarantined state, and auto-repairs."""
    from jarvis.core.lifecycle.types import ComponentLifecycleState, ComponentType
    from jarvis.orchestrator import JarvisOrchestrator

    orchestrator = JarvisOrchestrator(
        workspace_root=tmp_path,
        sessions_path=tmp_path / "sessions.json",
        conversations_path=tmp_path / "conversations.json",
    )

    # Verify all 5 specialists are registered in LifecycleManager
    for role in ("coding", "research", "computer", "personal", "analysis"):
        spec_id = f"specialist:{role}"
        rec = orchestrator.lifecycle.get_component(spec_id)
        assert rec is not None
        assert rec.component_type == ComponentType.SPECIALIST
        assert rec.state == ComponentLifecycleState.ENABLED
        assert orchestrator.lifecycle.is_available(spec_id) is True

    # Explicitly quarantine the coding specialist
    orchestrator.lifecycle.quarantine_component(
        "specialist:coding", "Injected transient test fault"
    )
    assert orchestrator.lifecycle.is_available("specialist:coding") is False

    # Orchestrator interaction should detect quarantine, execute automated self-healing, and succeed!
    resp = await orchestrator.interact("test_session_1", "list files in .")
    assert any(term in resp for term in ("Contents of", "Directory", "Entries", "Path"))

    # Verify coding specialist has been restored to ENABLED after auto-repair
    rec_after = orchestrator.lifecycle.get_component("specialist:coding")
    assert rec_after is not None
    assert rec_after.state == ComponentLifecycleState.ENABLED
    assert rec_after.total_invocations >= 1
