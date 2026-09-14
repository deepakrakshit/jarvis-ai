"""JARVIS Baseline LangGraph Workflow Engine.

Provides the foundational state graph initialization and deterministic verification for Stage 0.
"""

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph


class BaselineState(TypedDict):
    """Minimal baseline state schema for Stage 0 verification."""

    status: str
    step_count: int
    data: dict[str, Any]


def init_node(state: BaselineState) -> dict[str, Any]:
    """Execute baseline initialization step."""
    return {
        "status": "completed",
        "step_count": state.get("step_count", 0) + 1,
    }


def create_baseline_graph() -> Any:
    """Construct and compile an executable baseline LangGraph StateGraph."""
    workflow = StateGraph(BaselineState)
    workflow.add_node("initialize", init_node)
    workflow.set_entry_point("initialize")
    workflow.add_edge("initialize", END)
    return workflow.compile()
