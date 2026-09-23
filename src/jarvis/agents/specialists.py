"""Specialist Sub-Agent Role Configurations and Profiles.

Enforces Section 29 of ARCHITECTURE.md:
- Explicit specialization: Research, Coding, Browser, Verify.
- Least-privilege capability boundaries for each role.
- TaskClass mapping into the 6 approved model families.
"""

from typing import Any, Dict, List, Optional

from jarvis.agents.models import SubagentRole, SubagentSpec
from jarvis.cognition.model_router import TaskClass
from jarvis.contracts.task import Task
from jarvis.policy.firewall import (
    CAPABILITY_BROWSER_CLICK,
    CAPABILITY_BROWSER_NAVIGATE,
    CAPABILITY_BROWSER_SCREENSHOT,
    CAPABILITY_BROWSER_SNAPSHOT,
    CAPABILITY_BROWSER_TYPE,
    CAPABILITY_FILESYSTEM_LIST,
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_FILESYSTEM_WRITE,
    CAPABILITY_SHELL_EXECUTE,
    CAPABILITY_SYSTEM_INFO,
)


class SpecialistProfile:
    """Immutable capability and cognitive profile for a specialist subagent role."""

    def __init__(
        self,
        role: SubagentRole,
        default_capabilities: List[str],
        default_task_class: TaskClass,
        description: str,
    ) -> None:
        self.role = role
        self.default_capabilities = list(default_capabilities)
        self.default_task_class = default_task_class
        self.description = description

    def build_spec(
        self,
        parent_task: Task,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        allowed_capabilities: Optional[List[str]] = None,
        assigned_model: Optional[str] = None,
        token_budget: int = 8000,
        timeout_seconds: float = 60.0,
        depth: int = 1,
        max_depth: int = 2,
    ) -> SubagentSpec:
        """Create a validated specification constrained to the specialist profile."""
        # Restrict capabilities strictly to the profile's allowed subset (least privilege)
        if allowed_capabilities is not None:
            effective_caps = [c for c in allowed_capabilities if c in self.default_capabilities]
        else:
            effective_caps = list(self.default_capabilities)

        return SubagentSpec(
            parent_task_id=parent_task.task_id,
            session_id=parent_task.session_id,
            role=self.role,
            goal=goal,
            context=context or {},
            allowed_capabilities=effective_caps,
            assigned_model=assigned_model,
            token_budget=token_budget,
            timeout_seconds=timeout_seconds,
            depth=depth,
            max_depth=max_depth,
        )


RESEARCH_SPECIALIST = SpecialistProfile(
    role=SubagentRole.RESEARCH,
    default_capabilities=[
        CAPABILITY_FILESYSTEM_READ,
        CAPABILITY_FILESYSTEM_LIST,
        CAPABILITY_SYSTEM_INFO,
        CAPABILITY_BROWSER_SNAPSHOT,
    ],
    default_task_class=TaskClass.DEEP_REASONING,
    description="Information synthesis, code/document exploration, and technical analysis.",
)

CODING_SPECIALIST = SpecialistProfile(
    role=SubagentRole.CODING,
    default_capabilities=[
        CAPABILITY_FILESYSTEM_READ,
        CAPABILITY_FILESYSTEM_WRITE,
        CAPABILITY_FILESYSTEM_LIST,
        CAPABILITY_SHELL_EXECUTE,
    ],
    default_task_class=TaskClass.CODING,
    description="Surgical code authoring, refactoring, and test creation.",
)

BROWSER_SPECIALIST = SpecialistProfile(
    role=SubagentRole.BROWSER,
    default_capabilities=[
        CAPABILITY_BROWSER_NAVIGATE,
        CAPABILITY_BROWSER_SNAPSHOT,
        CAPABILITY_BROWSER_CLICK,
        CAPABILITY_BROWSER_TYPE,
        CAPABILITY_BROWSER_SCREENSHOT,
    ],
    default_task_class=TaskClass.SIMPLE_TOOL,
    description="Web browsing, DOM inspection, and structured data extraction.",
)

VERIFY_SPECIALIST = SpecialistProfile(
    role=SubagentRole.VERIFY,
    default_capabilities=[
        CAPABILITY_FILESYSTEM_READ,
        CAPABILITY_FILESYSTEM_LIST,
        CAPABILITY_SYSTEM_INFO,
        CAPABILITY_SHELL_EXECUTE,
    ],
    default_task_class=TaskClass.DEEP_REASONING,
    description="Independent post-condition verification, policy checks, and test runner.",
)

SPECIALIST_REGISTRY: Dict[SubagentRole, SpecialistProfile] = {
    SubagentRole.RESEARCH: RESEARCH_SPECIALIST,
    SubagentRole.CODING: CODING_SPECIALIST,
    SubagentRole.BROWSER: BROWSER_SPECIALIST,
    SubagentRole.VERIFY: VERIFY_SPECIALIST,
}
