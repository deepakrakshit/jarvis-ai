"""Tests for TaskBudget limits and enforcement."""

from datetime import UTC, datetime, timedelta

from jarvis.core.state.task import TaskBudget


def test_budget_initialization_defaults() -> None:
    """Verify default limits on TaskBudget."""
    budget = TaskBudget()
    assert budget.max_wall_time_seconds == 600.0
    assert budget.max_graph_steps == 50
    assert budget.started_at is None
    assert not budget.is_wall_time_exhausted()
    assert not budget.is_step_budget_exhausted(0)


def test_step_budget_exhaustion() -> None:
    """Verify step budget boundary checks."""
    budget = TaskBudget(max_graph_steps=10)
    assert not budget.is_step_budget_exhausted(9)
    assert budget.is_step_budget_exhausted(10)
    assert budget.is_step_budget_exhausted(15)


def test_wall_time_budget_exhaustion() -> None:
    """Verify wall-clock time limit exhaustion."""
    budget = TaskBudget(max_wall_time_seconds=30.0)
    budget.start()
    assert not budget.is_wall_time_exhausted()

    # Simulate started_at 31 seconds in the past
    budget.started_at = datetime.now(UTC) - timedelta(seconds=31)
    assert budget.is_wall_time_exhausted()
