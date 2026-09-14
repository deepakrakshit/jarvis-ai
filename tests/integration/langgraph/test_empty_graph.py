"""Baseline LangGraph Execution Verification.

Validates that an empty/baseline LangGraph workflow compiles and executes deterministically.
"""

import pytest

from jarvis.core.workflow import BaselineState, create_baseline_graph


@pytest.mark.integration
def test_baseline_langgraph_execution() -> None:
    """Verify execution of baseline LangGraph StateGraph."""
    graph = create_baseline_graph()
    initial_state: BaselineState = {
        "status": "initialized",
        "step_count": 0,
        "data": {"test_key": "test_val"},
    }

    final_state = graph.invoke(initial_state)

    assert final_state["status"] == "completed"
    assert final_state["step_count"] == 1
    assert final_state["data"]["test_key"] == "test_val"
