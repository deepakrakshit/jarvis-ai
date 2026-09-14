"""JARVIS Saga Compensation Workflows and Distributed Rollback.

Provides compensating transaction workflows for multi-step mutations across
heterogeneous tools that lack atomic two-phase commit (ARCHITECTURE.md Layer 14).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from jarvis.core.broker.types import CompensationFailedError
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class SagaStep(BaseModel):
    """An individual step in a Saga execution workflow."""

    step_id: UUID = Field(default_factory=uuid4)
    logical_effect_id: str
    tool_id: str
    forward_arguments: dict[str, Any] = Field(default_factory=dict)
    compensating_tool_id: str | None = None
    compensating_arguments: dict[str, Any] | None = None
    forward_executed: bool = False
    compensated: bool = False
    compensation_error: str | None = None


class SagaStepResult(BaseModel):
    """Outcome of an individual compensation execution."""

    step_id: UUID
    tool_id: str
    success: bool
    error: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SagaCompensationPlan(BaseModel):
    """Stack of forward operations requiring LIFO rollback upon failure."""

    plan_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    steps: list[SagaStep] = Field(default_factory=list)

    def add_step(
        self,
        logical_effect_id: str,
        tool_id: str,
        forward_arguments: dict[str, Any],
        compensating_tool_id: str | None = None,
        compensating_arguments: dict[str, Any] | None = None,
    ) -> SagaStep:
        """Register a forward step and its corresponding compensating action."""
        step = SagaStep(
            logical_effect_id=logical_effect_id,
            tool_id=tool_id,
            forward_arguments=forward_arguments,
            compensating_tool_id=compensating_tool_id,
            compensating_arguments=compensating_arguments,
            forward_executed=True,
        )
        self.steps.append(step)
        return step

    async def compensate_all(
        self,
        executor_fn: Callable[[str, dict[str, Any]], Any],
    ) -> list[SagaStepResult]:
        """Execute all compensating actions in reverse chronological order (LIFO).

        Args:
            executor_fn: Async or sync callable executing the compensating tool.

        Returns:
            list[SagaStepResult] of executed compensations.

        Raises:
            CompensationFailedError: If any compensating step fails.
        """
        results: list[SagaStepResult] = []
        # Rollback in LIFO order
        for step in reversed(self.steps):
            if not step.forward_executed or step.compensated:
                continue

            if not step.compensating_tool_id:
                logger.warning(
                    "saga_step_no_compensator",
                    step_id=str(step.step_id),
                    tool_id=step.tool_id,
                )
                continue

            logger.info(
                "saga_compensating_step",
                step_id=str(step.step_id),
                compensating_tool=step.compensating_tool_id,
            )

            try:
                import inspect

                args = step.compensating_arguments or {}
                if inspect.iscoroutinefunction(executor_fn):
                    await executor_fn(step.compensating_tool_id, args)
                else:
                    executor_fn(step.compensating_tool_id, args)

                step.compensated = True
                results.append(
                    SagaStepResult(
                        step_id=step.step_id,
                        tool_id=step.compensating_tool_id,
                        success=True,
                    )
                )
            except Exception as exc:
                step.compensation_error = str(exc)
                results.append(
                    SagaStepResult(
                        step_id=step.step_id,
                        tool_id=step.compensating_tool_id,
                        success=False,
                        error=str(exc),
                    )
                )
                logger.error(
                    "saga_compensation_failed",
                    step_id=str(step.step_id),
                    tool_id=step.compensating_tool_id,
                    error=str(exc),
                )
                raise CompensationFailedError(
                    logical_effect_id=step.logical_effect_id,
                    step_id=str(step.step_id),
                    reason=str(exc),
                ) from exc

        return results


# Function signature for dynamic compensator generators
CompensatorGenerator = Callable[[dict[str, Any], Any], tuple[str, dict[str, Any]] | None]


class CompensationRegistry:
    """Registry mapping tools to dynamic compensation generators."""

    def __init__(self) -> None:
        self._generators: dict[str, CompensatorGenerator] = {}

    def register(self, tool_id: str, generator: CompensatorGenerator) -> None:
        """Register a compensator generator for a specific tool ID."""
        self._generators[tool_id] = generator

    def resolve(
        self, tool_id: str, forward_args: dict[str, Any], forward_result: Any
    ) -> tuple[str, dict[str, Any]] | None:
        """Generate compensating tool and arguments for a completed forward action."""
        generator = self._generators.get(tool_id)
        if not generator:
            return None
        return generator(forward_args, forward_result)
