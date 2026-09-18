"""State machine and precondition governance for the JARVIS Computer Control Subsystem.

Enforces zero-trust execution barriers and prerequisite validation:
- Dependent computer actions fail closed if mandatory prerequisites fail or are denied
- Keystroke and mouse injection require verified foreground window correlation
- Transitions are strictly recorded for runtime auditing and deterministic recovery
"""

from __future__ import annotations

import time
from typing import Any

from jarvis.computer.models import (
    ComputerActionReasonCode,
    ComputerActionResult,
    ComputerActionState,
    ComputerActionStatus,
    WindowInfo,
)
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class ComputerActionStateMachine:
    """Zero-trust finite state machine governing computer control execution lifecycles."""

    def __init__(self) -> None:
        self.current_state: ComputerActionState = ComputerActionState.DESKTOP_UNKNOWN
        self.target_name: str | None = None
        self.target_hwnd: int | None = None
        self.target_pid: int | None = None
        self.target_window: WindowInfo | None = None
        self.last_action: str | None = None
        self.last_action_status: ComputerActionStatus | None = None
        self.last_action_result: ComputerActionResult | None = None
        self.last_action_timestamp: float = 0.0
        self.history: list[dict[str, Any]] = []

    def set_target(
        self,
        name: str | None = None,
        hwnd: int | None = None,
        pid: int | None = None,
        window: WindowInfo | None = None,
    ) -> None:
        """Register or update active execution target window and process metadata."""
        if name:
            self.target_name = name
        if hwnd is not None:
            self.target_hwnd = hwnd
        if pid is not None:
            self.target_pid = pid
        if window is not None:
            self.target_window = window
            self.target_hwnd = window.hwnd
            self.target_pid = window.process_id
            if not self.target_name:
                self.target_name = window.process_name or window.title

        if (
            self.target_hwnd is not None or self.target_name is not None
        ) and self.current_state == ComputerActionState.DESKTOP_UNKNOWN:
            self.transition_to(
                ComputerActionState.TARGET_RESOLVED,
                reason=f"Target registered: {self.target_name or self.target_hwnd}",
            )

    def transition_to(self, new_state: ComputerActionState, reason: str | None = None) -> None:
        """Advance the execution state machine to a new lifecycle phase."""
        old_state = self.current_state
        self.current_state = new_state
        logger.info(
            "computer_state_transition",
            old_state=old_state.value,
            new_state=new_state.value,
            reason=reason or "execution_advance",
            target=self.target_name or self.target_hwnd,
        )
        self.history.append(
            {
                "timestamp": time.time(),
                "event": "transition",
                "old_state": old_state.value,
                "new_state": new_state.value,
                "reason": reason,
                "target_hwnd": self.target_hwnd,
            }
        )

    def record_action_start(self, action: str, target: str | None = None) -> None:
        """Record the initiation of a governed computer action."""
        self.last_action = action
        self.transition_to(
            ComputerActionState.ACTION_EXECUTING,
            reason=f"Initiating action '{action}' on target '{target or self.target_name}'",
        )

    def record_action_result(self, result: ComputerActionResult) -> None:
        """Record the verified outcome of a computer action and transition states."""
        self.last_action = result.action
        self.last_action_status = result.status
        self.last_action_result = result
        self.last_action_timestamp = time.time()

        self.history.append(
            {
                "timestamp": self.last_action_timestamp,
                "event": "action_result",
                "action": result.action,
                "status": result.status.value,
                "verdict": result.verification_verdict,
                "reason_code": str(result.reason_code) if result.reason_code else None,
                "target": result.target,
            }
        )

        norm_action = result.action.lower()
        if result.status == ComputerActionStatus.DENIED:
            self.transition_to(
                ComputerActionState.FAILED,
                reason=f"Action '{result.action}' was DENIED: {result.reason_code or result.error}",
            )
        elif result.status == ComputerActionStatus.FAILED:
            self.transition_to(
                ComputerActionState.FAILED,
                reason=f"Action '{result.action}' FAILED: {result.reason_code or result.error}",
            )
        elif result.status in (ComputerActionStatus.EXECUTED, ComputerActionStatus.VERIFIED):
            if "focus" in norm_action:
                self.transition_to(
                    ComputerActionState.TARGET_FOCUSED,
                    reason=f"Window focus verified on {result.target or self.target_hwnd}",
                )
            elif "launch" in norm_action:
                self.transition_to(
                    ComputerActionState.TARGET_RESOLVED,
                    reason=f"Application launched and verified on {result.target or self.target_name}",
                )
            else:
                self.transition_to(
                    ComputerActionState.VERIFIED,
                    reason=f"Action '{result.action}' completed and verified.",
                )

    def validate_precondition(
        self,
        action: str,
        arguments: dict[str, Any],
        current_foreground_hwnd: int | None = None,
        is_window_valid: bool = True,
    ) -> tuple[bool, ComputerActionReasonCode | None, str | None, str | None]:
        """Evaluate strict zero-trust preconditions before physical execution.

        Returns:
            tuple[is_valid, reason_code, error_message, suggested_action]
        """
        norm_action = action.lower().strip()
        if norm_action.startswith("computer:"):
            norm_action = norm_action.replace("computer:", "")

        # 1. Failure cascade protection: fail closed if prerequisite action was DENIED or FAILED
        if self.last_action_status in (ComputerActionStatus.DENIED, ComputerActionStatus.FAILED):
            # Safe non-dependent actions that can re-establish state
            recovery_actions = {
                "launch_app",
                "focus_window",
                "screenshot",
                "list_windows",
                "list_apps",
                "list_processes",
                "inspect_ui",
                "ui:inspect",
                "resolve_target",
                "click_element",
                "wait",
            }
            if norm_action not in recovery_actions:
                last_err = (
                    self.last_action_result.message
                    if self.last_action_result
                    else "Prerequisite failed"
                )
                logger.warning(
                    "precondition_rejected_due_to_prior_failure",
                    action=norm_action,
                    prior_action=self.last_action,
                    prior_status=str(self.last_action_status),
                )
                return (
                    False,
                    ComputerActionReasonCode.PRECONDITION_NOT_SATISFIED,
                    (
                        f"Cannot execute '{action}': prerequisite action '{self.last_action}' "
                        f"resulted in {self.last_action_status.value} ({last_err}). "
                        "Resolve prerequisite focus or launch before proceeding."
                    ),
                    "focus_window",
                )

        # 2. Text Input Preconditions
        if norm_action in ("type", "type_text"):
            # Check target presence and focus
            if self.target_hwnd is not None:
                if not is_window_valid:
                    return (
                        False,
                        ComputerActionReasonCode.WINDOW_NOT_FOUND,
                        f"Target window {self.target_hwnd} does not exist or has closed.",
                        "launch_app",
                    )
                if (
                    current_foreground_hwnd is not None
                    and current_foreground_hwnd != self.target_hwnd
                ):
                    return (
                        False,
                        ComputerActionReasonCode.WINDOW_NOT_FOREGROUND,
                        (
                            f"Target window {self.target_hwnd} is not the active foreground window "
                            f"(current foreground: {current_foreground_hwnd})."
                        ),
                        "focus_window",
                    )

            # Check lifecycle state: typing requires target resolved and focused
            if self.current_state not in (
                ComputerActionState.TARGET_FOCUSED,
                ComputerActionState.VERIFIED,
                ComputerActionState.POST_ACTION_OBSERVING,
            ) and (current_foreground_hwnd is None or current_foreground_hwnd <= 0):
                return (
                    False,
                    ComputerActionReasonCode.PRECONDITION_NOT_SATISFIED,
                    (
                        "Cannot inject keystrokes: no active target window has been focused "
                        "and no valid foreground window was identified."
                    ),
                    "focus_window",
                )

        # 3. Mouse Click Preconditions
        if norm_action in ("click", "double_click", "right_click", "click_element"):
            x = arguments.get("x")
            y = arguments.get("y")
            query = (
                arguments.get("element_query")
                or arguments.get("query")
                or arguments.get("target")
                or arguments.get("semantic_role")
            )
            if x is None and y is None and not query:
                return (
                    False,
                    ComputerActionReasonCode.PRECONDITION_NOT_SATISFIED,
                    "Click action requires either (x, y) coordinates or a UI element query.",
                    "resolve_target",
                )

        # 4. Window State Preconditions
        if norm_action in ("minimize_window", "maximize_window", "close_window"):
            hwnd = arguments.get("hwnd")
            title = (
                arguments.get("window_title") or arguments.get("title") or arguments.get("query")
            )
            if not hwnd and not title and not self.target_hwnd and not self.target_name:
                return (
                    False,
                    ComputerActionReasonCode.PRECONDITION_NOT_SATISFIED,
                    f"Action '{norm_action}' requires a target window query or hwnd.",
                    "list_windows",
                )

        return True, None, None, None

    def reset(self) -> None:
        """Reset state machine to ungrounded initial desktop state."""
        self.current_state = ComputerActionState.DESKTOP_UNKNOWN
        self.target_name = None
        self.target_hwnd = None
        self.target_pid = None
        self.target_window = None
        self.last_action = None
        self.last_action_status = None
        self.last_action_result = None
        self.last_action_timestamp = 0.0
        self.history.clear()
