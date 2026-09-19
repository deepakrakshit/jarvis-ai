"""Specialist Sub-Agents Subsystem for JARVIS.

Implements Section 29 of ARCHITECTURE.md:
- Research, Coding, Browser, and Verify specialist agents.
- Least-privilege authority boundaries and scoped policy firewalls.
- Dynamic spawn depth validation.
- Parallel and sequential multi-agent coordination.
"""

from jarvis.agents.delegator import SubagentDelegator, subagent_delegator
from jarvis.agents.models import (
    SubagentResult,
    SubagentRole,
    SubagentSpec,
    SubagentStatus,
)
from jarvis.agents.specialists import (
    BROWSER_SPECIALIST,
    CODING_SPECIALIST,
    RESEARCH_SPECIALIST,
    SPECIALIST_REGISTRY,
    VERIFY_SPECIALIST,
    SpecialistProfile,
)

__all__ = [
    "SubagentRole",
    "SubagentStatus",
    "SubagentSpec",
    "SubagentResult",
    "SpecialistProfile",
    "RESEARCH_SPECIALIST",
    "CODING_SPECIALIST",
    "BROWSER_SPECIALIST",
    "VERIFY_SPECIALIST",
    "SPECIALIST_REGISTRY",
    "SubagentDelegator",
    "subagent_delegator",
]
