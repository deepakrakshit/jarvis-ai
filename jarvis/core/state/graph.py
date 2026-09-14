"""JARVIS LangGraph StateGraph Engine with Checkpoint Persistence and Resume Semantics.

Implements Stage 1 deterministic control plane, budget enforcement, and crash recovery.
"""

from typing import Any, Literal

from langgraph.graph import END, StateGraph

from jarvis.core.logging import get_logger
from jarvis.core.state.cancellation import CancellationManager
from jarvis.core.state.state import JarvisState
from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask
from jarvis.storage.task_store import TaskStore

logger = get_logger(__name__)


class TaskGraphEngine:
    """Deterministic LangGraph orchestrator backed by durable SQLite checkpoints."""

    def __init__(self, store: TaskStore) -> None:
        self.store = store
        self.cancellation = CancellationManager(store)
        self.graph = self._build_graph()

    def _persist_node_transition(
        self, state: JarvisState, node_name: str, target_status: TaskStatus
    ) -> JarvisState:
        """Advance task status, increment step counter, record event, and save checkpoint."""
        task: JarvisTask = state["task"]
        step = state.get("step_count", 0) + 1
        state["step_count"] = step

        event = task.transition_to(target_status)
        state["current_status"] = target_status

        self.store.save_task(task)
        self.store.record_event(event)
        self.store.save_checkpoint(task.task_id, node_name, step, state)

        logger.debug(
            "node_completed",
            node=node_name,
            task_id=task.task_id,
            status=target_status.value,
            step=step,
        )
        return state

    def _init_node(self, state: JarvisState) -> JarvisState:
        """Entry initialization node: CREATED -> QUEUED, or pass-through if resumed."""
        task: JarvisTask = state["task"]
        is_canceled, reason = self.cancellation.is_canceled(task.task_id)
        if is_canceled or state.get("is_canceled", False):
            state["is_canceled"] = True
            state["cancellation_reason"] = reason or state.get("cancellation_reason")
            return state

        if state.get("current_status") == TaskStatus.CREATED:
            return self._persist_node_transition(state, "init", TaskStatus.QUEUED)

        return state

    def _plan_node(self, state: JarvisState) -> JarvisState:
        """Planning node: QUEUED -> PLANNING."""
        state["proposed_intent"] = {
            "intent": "analyze_and_execute",
            "prompt": state["task"].input_prompt,
        }
        return self._persist_node_transition(state, "plan", TaskStatus.PLANNING)

    def _execute_node(self, state: JarvisState) -> JarvisState:
        """Execution node: PLANNING -> EXECUTING."""
        state["observations"] = [{"step": "plan_executed", "status": "ok"}]
        return self._persist_node_transition(state, "execute", TaskStatus.EXECUTING)

    def _verify_node(self, state: JarvisState) -> JarvisState:
        """Verification node: EXECUTING -> VERIFYING."""
        return self._persist_node_transition(state, "verify", TaskStatus.VERIFYING)

    def _complete_node(self, state: JarvisState) -> JarvisState:
        """Terminal completion node: VERIFYING -> COMPLETED."""
        state["task"].result = {
            "status": "success",
            "steps_executed": state.get("step_count", 0),
        }
        return self._persist_node_transition(state, "complete", TaskStatus.COMPLETED)

    def _cancel_node(self, state: JarvisState) -> JarvisState:
        """Terminal cancellation node: -> CANCELED."""
        reason = state.get("cancellation_reason") or "Task execution was aborted."
        task = state["task"]
        if not task.is_terminal():
            event = task.transition_to(TaskStatus.CANCELED, reason=reason)
            self.store.save_task(task)
            self.store.record_event(event)
        state["current_status"] = TaskStatus.CANCELED
        return state

    def _expire_node(self, state: JarvisState) -> JarvisState:
        """Terminal expiration node: -> EXPIRED."""
        task = state["task"]
        if not task.is_terminal():
            event = task.transition_to(TaskStatus.EXPIRED, reason="Task budget exhausted.")
            self.store.save_task(task)
            self.store.record_event(event)
        state["current_status"] = TaskStatus.EXPIRED
        return state

    def _route_from_init(
        self, state: JarvisState
    ) -> Literal["plan", "execute", "verify", "complete", "cancel", "expire"]:
        """Determine downstream target based on cancellation, budget, or resumption status."""
        task: JarvisTask = state["task"]

        # 1. Check Cancellation
        is_canceled, reason = self.cancellation.is_canceled(task.task_id)
        if is_canceled or state.get("is_canceled", False):
            state["is_canceled"] = True
            state["cancellation_reason"] = reason or state.get("cancellation_reason")
            return "cancel"

        # 2. Check Budget Limits
        steps = state.get("step_count", 0)
        if task.budget.is_step_budget_exhausted(steps) or task.budget.is_wall_time_exhausted():
            return "expire"

        # 3. Resumption dispatch
        status = state.get("current_status")
        if status == TaskStatus.PLANNING:
            return "execute"
        if status == TaskStatus.EXECUTING:
            return "verify"
        if status == TaskStatus.VERIFYING:
            return "complete"
        if status == TaskStatus.COMPLETED:
            return "complete"
        if status == TaskStatus.CANCELED:
            return "cancel"
        if status == TaskStatus.EXPIRED:
            return "expire"

        return "plan"

    def _route_condition(
        self, state: JarvisState
    ) -> Literal["proceed", "cancel", "expire", "error"]:
        """Evaluate budgets, cancellation signals, and errors before advancing to the next node."""
        task: JarvisTask = state["task"]

        # 1. Check Cancellation
        is_canceled, reason = self.cancellation.is_canceled(task.task_id)
        if is_canceled or state.get("is_canceled", False):
            state["is_canceled"] = True
            state["cancellation_reason"] = reason or state.get("cancellation_reason")
            return "cancel"

        # 2. Check Budget Limits (Wall time and graph steps)
        steps = state.get("step_count", 0)
        if task.budget.is_step_budget_exhausted(steps) or task.budget.is_wall_time_exhausted():
            return "expire"

        # 3. Check Unhandled Errors
        if state.get("errors"):
            return "error"

        return "proceed"

    def _build_graph(self) -> Any:
        """Construct the compiled LangGraph StateGraph."""
        workflow = StateGraph(JarvisState)

        # Register execution nodes
        workflow.add_node("init", self._init_node)
        workflow.add_node("plan", self._plan_node)
        workflow.add_node("execute", self._execute_node)
        workflow.add_node("verify", self._verify_node)
        workflow.add_node("complete", self._complete_node)
        workflow.add_node("cancel", self._cancel_node)
        workflow.add_node("expire", self._expire_node)

        # Define entry point
        workflow.set_entry_point("init")

        # Routing from init
        workflow.add_conditional_edges(
            "init",
            self._route_from_init,
            {
                "plan": "plan",
                "execute": "execute",
                "verify": "verify",
                "complete": "complete",
                "cancel": "cancel",
                "expire": "expire",
            },
        )

        # Routing from plan
        workflow.add_conditional_edges(
            "plan",
            self._route_condition,
            {
                "proceed": "execute",
                "cancel": "cancel",
                "expire": "expire",
                "error": "expire",
            },
        )

        # Routing from execute
        workflow.add_conditional_edges(
            "execute",
            self._route_condition,
            {
                "proceed": "verify",
                "cancel": "cancel",
                "expire": "expire",
                "error": "expire",
            },
        )

        # Routing from verify
        workflow.add_conditional_edges(
            "verify",
            self._route_condition,
            {
                "proceed": "complete",
                "cancel": "cancel",
                "expire": "expire",
                "error": "expire",
            },
        )

        # Terminal edges
        workflow.add_edge("complete", END)
        workflow.add_edge("cancel", END)
        workflow.add_edge("expire", END)

        return workflow.compile()

    def run_task(self, task: JarvisTask) -> JarvisState:
        """Execute a task through the compiled StateGraph from start."""
        self.store.save_task(task)
        initial_state: JarvisState = {
            "task_id": task.task_id,
            "task": task,
            "current_status": task.status,
            "step_count": 0,
            "messages": [{"role": "user", "content": task.input_prompt}],
            "proposed_intent": None,
            "observations": [],
            "is_canceled": False,
            "cancellation_reason": None,
            "errors": [],
            "artifacts": [],
            "metadata": task.metadata,
        }

        final_state: JarvisState = self.graph.invoke(initial_state)
        return final_state

    def resume_task(self, task_id: str) -> JarvisState:
        """Resume task execution from the last persisted checkpoint."""
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError(f"Task with ID '{task_id}' not found in store.")

        if task.is_terminal():
            logger.info("task_already_terminal", task_id=task_id, status=task.status.value)
            return {
                "task_id": task_id,
                "task": task,
                "current_status": task.status,
                "step_count": 0,
                "messages": [],
                "proposed_intent": None,
                "observations": [],
                "is_canceled": task.status == TaskStatus.CANCELED,
                "cancellation_reason": None,
                "errors": [],
                "artifacts": [],
                "metadata": task.metadata,
            }

        latest = self.store.get_latest_checkpoint(task_id)
        if not latest:
            # No checkpoints yet: run from beginning
            return self.run_task(task)

        chk_state: dict[str, Any] = latest["state"]
        resumed_state: JarvisState = {
            "task_id": task.task_id,
            "task": task,
            "current_status": task.status,
            "step_count": latest["step_count"],
            "messages": chk_state.get("messages", []),
            "proposed_intent": chk_state.get("proposed_intent"),
            "observations": chk_state.get("observations", []),
            "is_canceled": chk_state.get("is_canceled", False),
            "cancellation_reason": chk_state.get("cancellation_reason"),
            "errors": chk_state.get("errors", []),
            "artifacts": chk_state.get("artifacts", []),
            "metadata": chk_state.get("metadata", {}),
        }

        # Run remaining graph from resumed state
        final_state: JarvisState = self.graph.invoke(resumed_state)
        return final_state
