"""Specialist Sub-Agent Delegator and Coordinator.

Enforces Section 29 of ARCHITECTURE.md:
- Coordinator orchestrates specialist sub-agents (Research, Coding, Browser, Verify).
- Child agents do not inherit the parent's privileged authority (least privilege).
- Strict spawn depth limits prevent runaway recursive spawning.
- Comprehensive task lifecycle tracking linking child tasks to parent tasks.
"""

import asyncio
import time
from typing import List, Optional

from jarvis.actions.broker import ActionBroker
from jarvis.agents.models import SubagentResult, SubagentRole, SubagentSpec, SubagentStatus
from jarvis.cognition.model_router import ModelRouter, model_router
from jarvis.contracts.task import Task, TaskPriority, TaskState, TaskType
from jarvis.core.control_plane import ControlPlane, control_plane
from jarvis.policy.engine import PolicyEngine
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


def map_role_to_task_type(role: SubagentRole) -> TaskType:
    """Map a specialist role to its canonical task domain."""
    if role == SubagentRole.RESEARCH:
        return TaskType.RESEARCH
    elif role == SubagentRole.CODING:
        return TaskType.CODING
    elif role == SubagentRole.BROWSER:
        return TaskType.BROWSER_AUTOMATION
    elif role == SubagentRole.VERIFY:
        return TaskType.RESEARCH
    return TaskType.CONVERSATION


class SubagentDelegator:
    """Authoritative delegator dispatching and synthesizing specialist sub-agent tasks."""

    def __init__(
        self,
        control: Optional[ControlPlane] = None,
        database: Optional[DatabaseEngine] = None,
        router: Optional[ModelRouter] = None,
    ) -> None:
        self.control = control or control_plane
        self.db = database or db
        self.router = router or model_router

    async def spawn(self, spec: SubagentSpec) -> SubagentResult:
        """Spawn and execute a child specialist sub-agent under strict policy isolation."""
        start_time = time.perf_counter()

        # 1. Enforce Max Spawn Depth Limit (Recursion Safety)
        if spec.depth > spec.max_depth:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            err_msg = (
                f"Spawn depth violation: requested depth {spec.depth} "
                f"exceeds maximum allowed depth {spec.max_depth}"
            )
            logger.warning(f"Subagent spawn denied for {spec.task_id}: {err_msg}")
            return SubagentResult(
                task_id=spec.task_id,
                parent_task_id=spec.parent_task_id,
                role=spec.role,
                status=SubagentStatus.FAILED,
                error=err_msg,
                execution_duration_ms=duration_ms,
            )

        logger.info(
            f"Spawning [{spec.role.value}] specialist child task {spec.task_id} "
            f"(parent: {spec.parent_task_id}, depth: {spec.depth})"
        )

        # 2. Instantiate Child Task Record
        child_task = Task(
            task_id=spec.task_id,
            session_id=spec.session_id,
            parent_task_id=spec.parent_task_id,
            raw_intent=spec.goal,
            task_type=map_role_to_task_type(spec.role),
            priority=TaskPriority.NORMAL,
            token_budget=spec.token_budget,
            assigned_model=spec.assigned_model,
            assigned_agent=spec.role.value,
            state=TaskState.CREATED,
        )
        self.db.save_task(child_task)

        # 3. Create Scoped Policy and Broker (Authority Isolation / Least Privilege)
        scoped_policy = PolicyEngine(
            database=self.db,
            allowed_capabilities=set(spec.allowed_capabilities),
        )
        scoped_broker = ActionBroker(policy=scoped_policy, database=self.db)
        scoped_control = ControlPlane(
            broker=scoped_broker,
            policy=scoped_policy,
            database=self.db,
            router=self.router,
        )

        # 4. Execute Child Task with Deadline Timeout
        try:
            completed_task = await asyncio.wait_for(
                scoped_control.execute_task(child_task),
                timeout=spec.timeout_seconds,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            is_success = completed_task.state == TaskState.COMPLETED
            status = SubagentStatus.COMPLETED if is_success else SubagentStatus.FAILED

            return SubagentResult(
                task_id=spec.task_id,
                parent_task_id=spec.parent_task_id,
                role=spec.role,
                status=status,
                summary=completed_task.result_summary or "",
                output_payload={"result_summary": completed_task.result_summary},
                error=completed_task.error_message,
                execution_duration_ms=duration_ms,
            )
        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            err_msg = f"Specialist sub-agent execution timed out after {spec.timeout_seconds}s"
            child_task.transition_to(TaskState.FAILED, err_msg)
            child_task.error_message = err_msg
            self.db.save_task(child_task)

            logger.error(f"Child task {spec.task_id} timed out after {spec.timeout_seconds}s")
            return SubagentResult(
                task_id=spec.task_id,
                parent_task_id=spec.parent_task_id,
                role=spec.role,
                status=SubagentStatus.TIMEOUT,
                error=err_msg,
                execution_duration_ms=duration_ms,
            )

    async def dispatch_parallel(self, specs: List[SubagentSpec]) -> List[SubagentResult]:
        """Execute multiple specialist sub-agents concurrently."""
        tasks = [self.spawn(spec) for spec in specs]
        return list(await asyncio.gather(*tasks))

    async def dispatch_sequential(self, specs: List[SubagentSpec]) -> List[SubagentResult]:
        """Execute specialist sub-agents sequentially in specified order."""
        results: List[SubagentResult] = []
        for spec in specs:
            res = await self.spawn(spec)
            results.append(res)
        return results

    def synthesize(self, results: List[SubagentResult], parent_goal: str) -> str:
        """Synthesize individual specialist reports into an authoritative parent summary."""
        lines: List[str] = [
            f"### Sub-Agent Coordination Report: {parent_goal}\n",
            f"**Total Specialists Engaged:** {len(results)}",
        ]

        completed_count = sum(1 for r in results if r.status == SubagentStatus.COMPLETED)
        failed_count = sum(1 for r in results if r.status != SubagentStatus.COMPLETED)
        lines.append(f"**Results:** {completed_count} completed, {failed_count} failed\n")

        for result in results:
            lines.append(f"#### [{result.role.value}] Specialist `{result.task_id}`")
            lines.append(f"- **Status:** {result.status.value}")
            lines.append(f"- **Duration:** {result.execution_duration_ms:.1f}ms")
            if result.error:
                lines.append(f"- **Error:** {result.error}")
            if result.summary:
                lines.append(f"- **Summary:** {result.summary}")
            lines.append("")

        return "\n".join(lines).strip()


# Default singleton instance
subagent_delegator = SubagentDelegator()
