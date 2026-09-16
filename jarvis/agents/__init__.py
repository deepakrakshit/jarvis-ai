"""JARVIS Capability Specialists Subsystem.

Provides the 5 default capability specialists (Research, Coding, Computer, Personal, Analysis)
and the SpecialistRouter (ARCHITECTURE.md Layer 8 & Layer 10).
"""

from jarvis.agents.analysis import AnalysisSpecialist
from jarvis.agents.base import (
    BaseSpecialist,
    SpecialistManifest,
    SpecialistProposal,
    SpecialistResult,
    SpecialistRole,
    SpecialistScratchpad,
    parse_llm_json,
)
from jarvis.agents.coding import CodingSpecialist
from jarvis.agents.computer import ComputerSpecialist
from jarvis.agents.personal import PersonalSpecialist
from jarvis.agents.research import ResearchSpecialist
from jarvis.agents.router import SpecialistRouter

__all__ = [
    "AnalysisSpecialist",
    "BaseSpecialist",
    "CodingSpecialist",
    "ComputerSpecialist",
    "PersonalSpecialist",
    "ResearchSpecialist",
    "SpecialistManifest",
    "SpecialistProposal",
    "SpecialistResult",
    "SpecialistRole",
    "SpecialistRouter",
    "SpecialistScratchpad",
    "parse_llm_json",
]
