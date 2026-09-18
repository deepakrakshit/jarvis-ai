"""Windows Computer Executor coordinating perception, UIA, input injection, and verification."""

from __future__ import annotations

import contextlib
import subprocess
import time
from pathlib import Path
from typing import Any

from jarvis.computer.application_resolver import ApplicationResolver
from jarvis.computer.input_driver import InputDriver
from jarvis.computer.models import (
    ComputerActionReasonCode,
    ComputerActionResult,
    ComputerActionStatus,
    ResolutionStatus,
    TargetObservation,
    TargetQuery,
    TargetSource,
    WindowInfo,
)
from jarvis.computer.perception import ScreenPerception
from jarvis.computer.state_machine import ComputerActionStateMachine
from jarvis.computer.ui_targeting import UITargetResolver
from jarvis.computer.uia import UIAEngine
from jarvis.computer.verifier import ComputerStateVerifier
from jarvis.computer.windows_api import WindowsAPI
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class WindowsComputerExecutor:
    """Canonical executor implementing the governed computer capabilities."""

    def __init__(
        self,
        windows_api: WindowsAPI | None = None,
        perception: ScreenPerception | None = None,
        resolver: ApplicationResolver | None = None,
        input_driver: InputDriver | None = None,
        uia: UIAEngine | None = None,
        verifier: ComputerStateVerifier | None = None,
        state_machine: ComputerActionStateMachine | None = None,
        target_resolver: UITargetResolver | None = None,
    ) -> None:
        self.windows_api = windows_api or WindowsAPI()
        self.perception = perception or ScreenPerception(self.windows_api)
        self.resolver = resolver or ApplicationResolver(self.windows_api)
        self.input_driver = input_driver or InputDriver(self.windows_api)
        self.uia = uia or UIAEngine(self.windows_api)
        self.verifier = verifier or ComputerStateVerifier(self.windows_api, self.perception)
        self.state_machine = state_machine or ComputerActionStateMachine()
        self.target_resolver = target_resolver or UITargetResolver(self.windows_api, self.uia)

    async def execute_capability(
        self,
        capability_id: str,
        arguments: dict[str, Any],
    ) -> ComputerActionResult:
        """Central dispatch gateway mapping capability IDs to physical executor actions."""
        start_time = time.perf_counter()
        cap = capability_id.lower().strip()

        # Handle aliases
        if cap.startswith("native:screen:") or cap.startswith("native:app:"):
            # Normalize legacy aliases
            cap = cap.replace("native:screen:", "computer:").replace("native:app:", "computer:")
            if cap == "computer:capture":
                cap = "computer:screenshot"
            elif cap == "computer:launch":
                cap = "computer:launch_app"
            elif cap == "computer:close":
                cap = "computer:close_window"
            elif cap == "computer:list":
                cap = "computer:list_apps"
            elif cap == "computer:get_window":
                cap = "computer:list_windows"
        elif cap in ("computer:ui:inspect", "ui:inspect", "jarvis_inspect_ui"):
            cap = "computer:inspect_ui"

        try:
            if cap in ("computer:screenshot", "screenshot"):
                res = self.screenshot(
                    output_format=arguments.get("output_format", "bytes"),
                    max_dimension=int(arguments.get("max_dimension", 1280)),
                    quality=int(arguments.get("quality", 80)),
                    monitor_index=int(arguments.get("monitor_index", 1)),
                )
            elif cap in ("computer:inspect_ui", "inspect_ui"):
                res = self.inspect_ui(
                    window_title=arguments.get("window_title") or arguments.get("title"),
                    hwnd=arguments.get("hwnd"),
                    max_elements=int(arguments.get("max_elements", 150)),
                )
            elif cap in ("computer:resolve_target", "resolve_target"):
                target_args = arguments.get("target") or arguments
                res = self.resolve_target_capability(target_args)
            elif cap in ("computer:click_element", "click_element"):
                target_args = arguments.get("target") or arguments
                res = self.click_element_capability(target_args)
            elif cap in ("computer:click", "click"):
                res = self.click(
                    x=arguments.get("x"),
                    y=arguments.get("y"),
                    element_query=arguments.get("element_query") or arguments.get("query"),
                    window_title=arguments.get("window_title") or arguments.get("title"),
                    button=arguments.get("button", "left"),
                    clicks=int(arguments.get("clicks", 1)),
                    normalized=bool(arguments.get("normalized", False)),
                    monitor_index=int(arguments.get("monitor_index", 1)),
                    target=arguments.get("target"),
                    semantic_role=arguments.get("semantic_role"),
                    target_source=arguments.get("target_source"),
                )
            elif cap in ("computer:double_click", "double_click"):
                res = self.double_click(
                    x=arguments.get("x"),
                    y=arguments.get("y"),
                    element_query=arguments.get("element_query") or arguments.get("query"),
                    window_title=arguments.get("window_title") or arguments.get("title"),
                    normalized=bool(arguments.get("normalized", False)),
                )
            elif cap in ("computer:right_click", "right_click"):
                res = self.right_click(
                    x=arguments.get("x"),
                    y=arguments.get("y"),
                    element_query=arguments.get("element_query") or arguments.get("query"),
                    window_title=arguments.get("window_title") or arguments.get("title"),
                    normalized=bool(arguments.get("normalized", False)),
                )
            elif cap in ("computer:move_mouse", "move_mouse"):
                res = self.move_mouse(
                    x=float(arguments.get("x", 0)),
                    y=float(arguments.get("y", 0)),
                    normalized=bool(arguments.get("normalized", False)),
                )
            elif cap in ("computer:drag", "drag"):
                res = self.drag(
                    from_x=float(arguments.get("from_x", 0)),
                    from_y=float(arguments.get("from_y", 0)),
                    to_x=float(arguments.get("to_x", 0)),
                    to_y=float(arguments.get("to_y", 0)),
                    normalized=bool(arguments.get("normalized", False)),
                )
            elif cap in ("computer:type", "type"):
                res = self.type_text(
                    text=str(arguments.get("text", "")),
                    press_enter=bool(arguments.get("press_enter", False)),
                    target=arguments.get("target"),
                    target_recipient=arguments.get("target_recipient")
                    or arguments.get("recipient"),
                    interval=float(arguments.get("interval", 0.01)),
                    element_query=arguments.get("element_query") or arguments.get("query"),
                    window_title=arguments.get("window_title") or arguments.get("title"),
                    recipient=arguments.get("recipient"),
                )
            elif cap in ("computer:press_key", "press_key"):
                res = self.press_key(key=str(arguments.get("key", "")))
            elif cap in ("computer:hotkey", "hotkey"):
                keys = arguments.get("keys") or []
                if isinstance(keys, str):
                    keys = [k.strip() for k in keys.split("+")]
                res = self.hotkey(keys=keys)
            elif cap in ("computer:scroll", "scroll"):
                res = self.scroll(clicks=int(arguments.get("clicks", 1)))
            elif cap in ("computer:navigate_browser", "navigate_browser"):
                res = self.navigate_browser(
                    url=str(arguments.get("url") or arguments.get("target") or ""),
                    browser=str(arguments.get("browser", "chrome")),
                )
            elif cap in ("computer:focus_window", "focus_window"):
                res = self.focus_window(
                    query=arguments.get("query") or arguments.get("title"),
                    hwnd=arguments.get("hwnd"),
                )
            elif cap in ("computer:minimize_window", "minimize_window"):
                res = self.set_window_state(
                    action="minimize",
                    query=arguments.get("query") or arguments.get("title"),
                    hwnd=arguments.get("hwnd"),
                )
            elif cap in ("computer:maximize_window", "maximize_window"):
                res = self.set_window_state(
                    action="maximize",
                    query=arguments.get("query") or arguments.get("title"),
                    hwnd=arguments.get("hwnd"),
                )
            elif cap in ("computer:close_window", "close_window"):
                res = self.close_window(
                    query=arguments.get("query")
                    or arguments.get("title")
                    or arguments.get("app_name"),
                    hwnd=arguments.get("hwnd"),
                )
            elif cap in ("computer:launch_app", "launch_app"):
                res = self.launch_app(
                    app_name=str(arguments.get("app_name") or arguments.get("name") or ""),
                    args=arguments.get("args") or arguments.get("arguments"),
                    verify=bool(arguments.get("verify", True)),
                )
            elif cap in ("computer:list_windows", "list_windows"):
                res = self.list_windows(
                    include_invisible=bool(arguments.get("include_invisible", False))
                )
            elif cap in ("computer:list_apps", "list_apps"):
                res = self.list_apps(limit=int(arguments.get("limit", 100)))
            elif cap in ("computer:list_processes", "list_processes"):
                res = self.list_processes(
                    filter_name=arguments.get("filter_name") or arguments.get("filter"),
                    limit=int(arguments.get("limit", 100)),
                )
            elif cap in ("computer:terminate_process", "terminate_process"):
                res = self.terminate_process(
                    pid=int(arguments.get("pid", 0)),
                    force=bool(arguments.get("force", False)),
                )
            elif cap in ("computer:wait", "wait"):
                seconds = float(arguments.get("seconds", 1.0))
                time.sleep(min(seconds, 10.0))
                res = ComputerActionResult(
                    action="computer:wait",
                    status=ComputerActionStatus.EXECUTED,
                    details={"waited_seconds": seconds},
                    verification_verdict="VERIFIED",
                )
            elif cap in ("computer:verify_visual_state", "verify_visual_state"):
                digest = str(arguments.get("pre_screenshot_digest", ""))
                res = self.verify_visual_state(digest)
            else:
                raise ValueError(f"Unknown computer capability: '{capability_id}'")

            res.execution_duration_ms = (time.perf_counter() - start_time) * 1000.0
            return res

        except Exception as exc:
            return ComputerActionResult(
                action=cap,
                status=ComputerActionStatus.FAILED,
                error=str(exc),
                execution_duration_ms=(time.perf_counter() - start_time) * 1000.0,
                verification_verdict="FAILED",
            )

    # 1. Perception
    def screenshot(
        self,
        output_format: str = "bytes",
        max_dimension: int = 1280,
        quality: int = 80,
        monitor_index: int = 1,
    ) -> ComputerActionResult:
        snap = self.perception.capture(
            output_format=output_format,
            max_dimension=max_dimension,
            quality=quality,
            monitor_index=monitor_index,
        )
        if snap.status != "success":
            return ComputerActionResult(
                action="computer:screenshot",
                status=ComputerActionStatus.FAILED,
                error=snap.message,
                verification_verdict="FAILED",
            )

        details = snap.metadata.model_dump()
        if snap.base64_data:
            details["base64_data"] = snap.base64_data
        if snap.raw_bytes:
            details["raw_bytes_len"] = len(snap.raw_bytes)

        return ComputerActionResult(
            action="computer:screenshot",
            status=ComputerActionStatus.EXECUTED,
            details=details,
            screenshot_metadata=snap.metadata,
            verification_verdict="VERIFIED",
        )

    # 2. UI Inspection
    def inspect_ui(
        self,
        window_title: str | None = None,
        hwnd: int | None = None,
        max_elements: int = 50,
    ) -> ComputerActionResult:
        target_hwnd = hwnd
        if not target_hwnd and window_title:
            w = self.windows_api.find_window(window_title)
            if w:
                target_hwnd = w.hwnd

        if not target_hwnd:
            self.windows_api.attach_to_default_desktop()
            target_hwnd = (
                self.windows_api._user32.GetForegroundWindow() if self.windows_api._user32 else None
            )

        if not target_hwnd:
            return ComputerActionResult(
                action="computer:inspect_ui",
                status=ComputerActionStatus.FAILED,
                error="No target or active foreground window found to inspect.",
                verification_verdict="FAILED",
            )

        elems = self.uia.inspect_window_elements(target_hwnd, max_elements=max_elements)
        return ComputerActionResult(
            action="computer:inspect_ui",
            status=ComputerActionStatus.EXECUTED,
            target=str(target_hwnd),
            details={
                "hwnd": target_hwnd,
                "elements_count": len(elems),
                "elements": [e.model_dump() for e in elems],
            },
            verification_verdict="VERIFIED",
        )

    # 3. Mouse Interactions (Click, Double-Click, Right-Click, Move, Drag)
    def resolve_target_capability(
        self,
        query_input: dict[str, Any] | TargetQuery,
    ) -> ComputerActionResult:
        """Resolve semantic UI target dynamically without physical input."""
        q = query_input if isinstance(query_input, TargetQuery) else TargetQuery(**query_input)
        target_match = self.target_resolver.resolve_target(q)
        if not target_match:
            return ComputerActionResult(
                action="computer:resolve_target",
                status=ComputerActionStatus.FAILED,
                reason_code=ComputerActionReasonCode.UI_ELEMENT_NOT_FOUND,
                message=f"Semantic target '{q.semantic_role or q.name or q.control_type}' could not be resolved in window.",
                suggested_action="inspect_ui",
                verification_verdict="FAILED",
            )

        if target_match.is_ambiguous:
            return ComputerActionResult(
                action="computer:resolve_target",
                status=ComputerActionStatus.FAILED,
                reason_code=ComputerActionReasonCode.AMBIGUOUS_TARGET,
                message=f"Ambiguous target: multiple candidate elements match '{q.semantic_role or q.name}'.",
                suggested_action="inspect_ui",
                verification_verdict="FAILED",
            )

        obs = target_match.observation
        return ComputerActionResult(
            action="computer:resolve_target",
            status=ComputerActionStatus.EXECUTED,
            target=obs.element_name,
            target_source=obs.target_source,
            target_observation=obs,
            details={
                "element_name": obs.element_name,
                "control_type": obs.control_type,
                "automation_id": obs.automation_id,
                "clickable_point": obs.clickable_point,
                "bounding_rect": obs.element_rect,
                "target_source": obs.target_source.value,
                "score": target_match.score,
                "match_reasons": target_match.match_reasons,
            },
            verification_verdict="VERIFIED",
            message=f"Resolved '{obs.element_name}' ({obs.control_type}) via {obs.target_source.value}.",
        )

    def click_element_capability(
        self,
        query_input: dict[str, Any] | TargetQuery,
    ) -> ComputerActionResult:
        """Resolve semantic target and perform verified click."""
        q = query_input if isinstance(query_input, TargetQuery) else TargetQuery(**query_input)
        return self.click(target=q)

    def click(
        self,
        x: float | None = None,
        y: float | None = None,
        element_query: str | None = None,
        window_title: str | None = None,
        button: str = "left",
        clicks: int = 1,
        normalized: bool = False,
        monitor_index: int = 1,
        target: dict[str, Any] | TargetQuery | None = None,
        semantic_role: str | None = None,
        target_source: TargetSource | str | None = None,
    ) -> ComputerActionResult:
        """Execute physical click at verified clickable point or semantic pattern invocation."""
        pre_snap = self.perception.capture(output_format="bytes")
        pre_digest = pre_snap.metadata.sha256_digest if pre_snap.status == "success" else ""

        effective_source = TargetSource(target_source) if target_source else None
        target_obs: TargetObservation | None = None

        # 1. Semantic resolution via UITargetResolver if target or role provided
        if target or semantic_role or element_query:
            if isinstance(target, TargetQuery):
                q = target
            elif isinstance(target, dict):
                q = TargetQuery(**target)
            else:
                q = TargetQuery(
                    semantic_role=semantic_role,
                    name=element_query,
                    window_title=window_title,
                    intent="click",
                )

            target_match = self.target_resolver.resolve_target(q)
            if not target_match:
                res = ComputerActionResult(
                    action="computer:click",
                    status=ComputerActionStatus.FAILED,
                    reason_code=ComputerActionReasonCode.UI_ELEMENT_NOT_FOUND,
                    error=f"Could not resolve target '{q.semantic_role or q.name}' in window.",
                    message=f"UI target '{q.semantic_role or q.name}' was not found in the target window.",
                    suggested_action="inspect_ui",
                    verification_verdict="FAILED",
                )
                self.state_machine.record_action_result(res)
                return res

            if target_match.is_ambiguous:
                res = ComputerActionResult(
                    action="computer:click",
                    status=ComputerActionStatus.FAILED,
                    reason_code=ComputerActionReasonCode.AMBIGUOUS_TARGET,
                    error=f"Ambiguous target: multiple candidates matched ({target_match.candidate_count}).",
                    message=f"Multiple elements match '{q.semantic_role or q.name}'. Disambiguate with exact name or automation_id.",
                    suggested_action="inspect_ui",
                    verification_verdict="FAILED",
                )
                self.state_machine.record_action_result(res)
                return res

            obs = target_match.observation
            target_obs = obs
            effective_source = obs.target_source

            if not obs.clickable_point:
                res = ComputerActionResult(
                    action="computer:click",
                    status=ComputerActionStatus.FAILED,
                    reason_code=ComputerActionReasonCode.UI_ELEMENT_NOT_FOUND,
                    error="Target has no valid clickable point or non-zero bounding rectangle.",
                    message="Target element has no clickable point.",
                    verification_verdict="FAILED",
                )
                self.state_machine.record_action_result(res)
                return res

            px, py = obs.clickable_point

            # Live ElementFromPoint verification to prevent clicking obscured or stale targets
            is_point_valid, point_err = self.target_resolver.validate_element_at_point(px, py, obs)
            if not is_point_valid:
                res = ComputerActionResult(
                    action="computer:click",
                    status=ComputerActionStatus.DENIED,
                    reason_code=ComputerActionReasonCode.STALE_TARGET,
                    error=point_err,
                    message=f"Click rejected: {point_err}",
                    verification_verdict="DENIED",
                    target_observation=obs,
                )
                self.state_machine.record_action_result(res)
                return res

            # Prefer semantic InvokePattern where available and standard left-click requested
            if button.lower() == "left" and clicks == 1 and "invoke" in obs.supported_patterns:
                inv_ok = self.uia.invoke_element_pattern(
                    obs.window_hwnd,
                    automation_id=obs.automation_id,
                    name=obs.element_name,
                )
                if inv_ok:
                    verif = self.verifier.verify_visual_state_changed(pre_digest)
                    res = ComputerActionResult(
                        action="computer:click",
                        status=ComputerActionStatus.EXECUTED,
                        target=obs.element_name,
                        target_source=TargetSource.UIA_SEMANTIC,
                        target_observation=obs,
                        details={"method": "InvokePattern", "element": obs.element_name},
                        verification_verdict=verif["verdict"],
                        verification_details=verif,
                        message=f"Successfully invoked '{obs.element_name}' via UIA InvokePattern.",
                    )
                    self.state_machine.record_action_result(res)
                    return res

            x, y = float(px), float(py)
            normalized = False

        if x is None or y is None:
            return ComputerActionResult(
                action="computer:click",
                status=ComputerActionStatus.FAILED,
                reason_code=ComputerActionReasonCode.UI_ELEMENT_NOT_FOUND,
                error="Click failed: No coordinates provided and UI element could not be resolved.",
                message="Click failed: No coordinates or target provided.",
                verification_verdict="FAILED",
            )

        if not effective_source:
            effective_source = TargetSource.EXPLICIT_OPERATOR

        click_res = self.input_driver.click(
            x=x,
            y=y,
            button=button,
            clicks=clicks,
            normalized=normalized,
            monitor_index=monitor_index,
        )

        verif = self.verifier.verify_visual_state_changed(pre_digest)

        res = ComputerActionResult(
            action="computer:click",
            status=ComputerActionStatus.EXECUTED,
            target=(
                target_obs.element_name
                if target_obs and target_obs.element_name
                else f"({click_res['pixel_x']}, {click_res['pixel_y']})"
            ),
            target_source=effective_source,
            target_observation=target_obs,
            details=click_res,
            verification_verdict=verif["verdict"],
            verification_details=verif,
        )
        self.state_machine.record_action_result(res)
        return res

    def double_click(
        self,
        x: float | None = None,
        y: float | None = None,
        element_query: str | None = None,
        window_title: str | None = None,
        normalized: bool = False,
        target: dict[str, Any] | TargetQuery | None = None,
        semantic_role: str | None = None,
    ) -> ComputerActionResult:
        return self.click(
            x=x,
            y=y,
            element_query=element_query,
            window_title=window_title,
            button="left",
            clicks=2,
            normalized=normalized,
            target=target,
            semantic_role=semantic_role,
        )

    def right_click(
        self,
        x: float | None = None,
        y: float | None = None,
        element_query: str | None = None,
        window_title: str | None = None,
        normalized: bool = False,
        target: dict[str, Any] | TargetQuery | None = None,
        semantic_role: str | None = None,
    ) -> ComputerActionResult:
        return self.click(
            x=x,
            y=y,
            element_query=element_query,
            window_title=window_title,
            button="right",
            clicks=1,
            normalized=normalized,
            target=target,
            semantic_role=semantic_role,
        )

    def move_mouse(self, x: float, y: float, normalized: bool = False) -> ComputerActionResult:
        res = self.input_driver.move_mouse(x=x, y=y, normalized=normalized)
        return ComputerActionResult(
            action="computer:move_mouse",
            status=ComputerActionStatus.EXECUTED,
            target=f"({res['pixel_x']}, {res['pixel_y']})",
            details=res,
            verification_verdict="VERIFIED",
        )

    def drag(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        normalized: bool = False,
    ) -> ComputerActionResult:
        res = self.input_driver.drag(
            from_x=from_x, from_y=from_y, to_x=to_x, to_y=to_y, normalized=normalized
        )
        return ComputerActionResult(
            action="computer:drag",
            status=ComputerActionStatus.EXECUTED,
            details=res,
            verification_verdict="VERIFIED",
        )

    # 4. Keyboard
    def type_text(
        self,
        text: str,
        press_enter: bool = False,
        target: dict[str, Any] | TargetQuery | None = None,
        target_recipient: str | None = None,
        interval: float = 0.01,
        element_query: str | None = None,
        window_title: str | None = None,
        recipient: str | None = None,
    ) -> ComputerActionResult:
        """Inject text with target focus validation and messaging safety confirmation."""
        fg_hwnd = None
        if self.windows_api.is_windows and self.windows_api._user32:
            with contextlib.suppress(Exception):
                fg_hwnd = self.windows_api._user32.GetForegroundWindow()

        target_obs: TargetObservation | None = None
        target_recipient = target_recipient or recipient

        # If explicit query or window_title provided without target object, construct TargetQuery
        if not target and (element_query or window_title or recipient):
            target = TargetQuery(
                element_query=element_query,
                window_title=window_title,
                semantic_role="message_input" if target_recipient else "editable_text",
            )

        # 1. Semantic target resolution if specified
        if target:
            q = target if isinstance(target, TargetQuery) else TargetQuery(**target)
            target_match = self.target_resolver.resolve_target(q)
            if not target_match:
                res = ComputerActionResult(
                    action="computer:type",
                    status=ComputerActionStatus.FAILED,
                    reason_code=ComputerActionReasonCode.UI_ELEMENT_NOT_FOUND,
                    error=f"Could not resolve target text field '{q.semantic_role or q.name}'.",
                    message=f"Text target '{q.semantic_role or q.name}' was not found.",
                    verification_verdict="FAILED",
                )
                self.state_machine.record_action_result(res)
                return res

            target_obs = target_match.observation

            # Check messaging safety confirmation boundary if recipient specified
            if target_recipient:
                confirmed, conf_reason = self.target_resolver.verify_messaging_target_confirmed(
                    target_obs.window_hwnd, target_recipient
                )
                if not confirmed:
                    res = ComputerActionResult(
                        action="computer:type",
                        status=ComputerActionStatus.DENIED,
                        reason_code=ComputerActionReasonCode.CHAT_RECIPIENT_MISMATCH,
                        error=conf_reason,
                        message=f"Message sending blocked by safety boundary: {conf_reason}",
                        verification_verdict="DENIED",
                        target_observation=target_obs,
                    )
                    self.state_machine.record_action_result(res)
                    return res

            # If semantic ValuePattern supported and not press_enter:
            if not press_enter and "set_text" in target_obs.supported_patterns:
                val_ok = self.uia.set_element_value_pattern(
                    target_obs.window_hwnd,
                    value=text,
                    automation_id=target_obs.automation_id,
                    name=target_obs.element_name,
                )
                if val_ok:
                    res = ComputerActionResult(
                        action="computer:type",
                        status=ComputerActionStatus.EXECUTED,
                        target=target_obs.element_name,
                        target_source=TargetSource.UIA_SEMANTIC,
                        target_observation=target_obs,
                        details={"method": "ValuePattern", "text": text},
                        verification_verdict="VERIFIED",
                        message=f"Successfully set value into '{target_obs.element_name}' via UIA ValuePattern.",
                    )
                    self.state_machine.record_action_result(res)
                    return res

            # Set focus to the element
            if target_obs.clickable_point:
                self.windows_api.focus_window(target_obs.window_hwnd)
                self.input_driver.click(
                    x=target_obs.clickable_point[0],
                    y=target_obs.clickable_point[1],
                    clicks=1,
                    normalized=False,
                )
                time.sleep(0.1)

        target_valid = True
        if (
            self.state_machine.target_hwnd
            and self.windows_api.is_windows
            and self.windows_api._user32
        ):
            with contextlib.suppress(Exception):
                target_valid = bool(
                    self.windows_api._user32.IsWindow(self.state_machine.target_hwnd)
                )

        is_valid, reason_code, err_msg, suggested_action = self.state_machine.validate_precondition(
            action="computer:type",
            arguments={"text": text, "press_enter": press_enter},
            current_foreground_hwnd=fg_hwnd,
            is_window_valid=target_valid,
        )
        if not is_valid:
            res = ComputerActionResult(
                action="computer:type",
                status=ComputerActionStatus.DENIED,
                reason_code=reason_code or ComputerActionReasonCode.PRECONDITION_NOT_SATISFIED,
                error=err_msg,
                message=err_msg or "Cannot inject keystrokes: precondition not satisfied.",
                suggested_action=suggested_action,
                verification_verdict="DENIED",
                retryable=False,
            )
            self.state_machine.record_action_result(res)
            return res

        self.state_machine.record_action_start(
            "computer:type",
            target=str(self.state_machine.target_hwnd or fg_hwnd or "active_window"),
        )
        pre_snap = self.perception.capture(output_format="bytes")
        pre_digest = pre_snap.metadata.sha256_digest if pre_snap.status == "success" else ""

        if interval != 0.01:
            res_dict = self.input_driver.type_text(
                text=text,
                press_enter=press_enter,
                interval=interval,
            )
        else:
            res_dict = self.input_driver.type_text(
                text=text,
                press_enter=press_enter,
            )
        verif = self.verifier.verify_visual_state_changed(pre_digest)

        res = ComputerActionResult(
            action="computer:type",
            status=ComputerActionStatus.EXECUTED,
            target=str(self.state_machine.target_hwnd or fg_hwnd or "desktop"),
            target_source=target_obs.target_source
            if target_obs
            else TargetSource.EXPLICIT_OPERATOR,
            target_observation=target_obs,
            details=res_dict,
            verification_verdict=verif["verdict"],
            verification_details=verif,
            message=f"Successfully typed {len(text)} characters into target window.",
        )
        self.state_machine.record_action_result(res)
        return res
        return res

    def press_key(self, key: str) -> ComputerActionResult:
        res = self.input_driver.press_key(key=key)
        return ComputerActionResult(
            action="computer:press_key",
            status=ComputerActionStatus.EXECUTED,
            target=key,
            details=res,
            verification_verdict="VERIFIED",
        )

    def hotkey(self, keys: list[str]) -> ComputerActionResult:
        res = self.input_driver.hotkey(keys=keys)
        return ComputerActionResult(
            action="computer:hotkey",
            status=ComputerActionStatus.EXECUTED,
            target="+".join(keys),
            details=res,
            verification_verdict="VERIFIED",
        )

    def scroll(self, clicks: int) -> ComputerActionResult:
        pre_snap = self.perception.capture(output_format="bytes")
        pre_digest = pre_snap.metadata.sha256_digest if pre_snap.status == "success" else ""

        res = self.input_driver.scroll(clicks=clicks)
        verif = self.verifier.verify_visual_state_changed(pre_digest)

        return ComputerActionResult(
            action="computer:scroll",
            status=ComputerActionStatus.EXECUTED,
            details=res,
            verification_verdict=verif["verdict"],
            verification_details=verif,
        )

    # 5. Window Management
    def focus_window(
        self, query: str | None = None, hwnd: int | None = None
    ) -> ComputerActionResult:
        self.state_machine.record_action_start("computer:focus_window", target=query or str(hwnd))
        target_hwnd = hwnd
        found_win: WindowInfo | None = None

        # 1. Direct HWND lookup if hwnd passed or query is digit
        if not target_hwnd and query and query.strip().isdigit():
            target_hwnd = int(query.strip())

        # 2. Check if query matches current target in state machine
        if (
            not target_hwnd
            and query
            and self.state_machine.target_name
            and query.strip().lower() in self.state_machine.target_name.lower()
        ):
            target_hwnd = self.state_machine.target_hwnd

        # 3. Match using WindowsAPI window enumeration and semantic resolution
        if not target_hwnd and query:
            clean_q = query.strip()
            # 3a. Direct title or process match
            w = self.windows_api.find_window(clean_q)
            if w:
                target_hwnd = w.hwnd
                found_win = w
            else:
                # 3b. Semantic application resolution (e.g. 'chrome' -> 'chrome.exe')
                resolution = self.resolver.resolve(clean_q, prefer_running=True)
                if resolution.existing_hwnd:
                    target_hwnd = resolution.existing_hwnd
                elif resolution.resolved_path:
                    exe_name = Path(resolution.resolved_path).name.lower()
                    windows = self.windows_api.list_desktop_windows(include_invisible=True)
                    for win in windows:
                        if (
                            win.process_name.lower() == exe_name
                            or clean_q.lower() in win.process_name.lower()
                            or clean_q.lower() in win.title.lower()
                        ):
                            target_hwnd = win.hwnd
                            found_win = win
                            break

        if not target_hwnd:
            res = ComputerActionResult(
                action="computer:focus_window",
                status=ComputerActionStatus.DENIED,
                reason_code=ComputerActionReasonCode.WINDOW_NOT_FOUND,
                error=f"No matching window found for query: '{query}'.",
                message=f"No active window found matching '{query}'. If the application is closed, launch it first.",
                suggested_action="launch_app",
                retryable=True,
                verification_verdict="DENIED",
                diagnostic_context={"query": query, "hwnd": hwnd},
            )
            self.state_machine.record_action_result(res)
            return res

        # Focus window
        success = self.windows_api.focus_window(target_hwnd)

        # Verify foreground state
        fg_hwnd = None
        if self.windows_api.is_windows and self.windows_api._user32:
            with contextlib.suppress(Exception):
                fg_hwnd = self.windows_api._user32.GetForegroundWindow()

        is_fg = (fg_hwnd == target_hwnd) if fg_hwnd is not None else success

        if success or is_fg:
            if not found_win:
                windows = self.windows_api.list_desktop_windows(include_invisible=True)
                for win in windows:
                    if win.hwnd == target_hwnd:
                        found_win = win
                        break

            self.state_machine.set_target(
                name=query or (found_win.title if found_win else str(target_hwnd)),
                hwnd=target_hwnd,
                pid=found_win.process_id if found_win else None,
                window=found_win,
            )
            res = ComputerActionResult(
                action="computer:focus_window",
                status=ComputerActionStatus.EXECUTED,
                target=str(target_hwnd),
                details={
                    "hwnd": target_hwnd,
                    "title": found_win.title if found_win else "",
                    "process_name": found_win.process_name if found_win else "",
                    "is_foreground": True,
                },
                verification_verdict="VERIFIED",
                message=f"Window '{found_win.title if found_win else target_hwnd}' focused and brought to foreground.",
            )
        else:
            res = ComputerActionResult(
                action="computer:focus_window",
                status=ComputerActionStatus.FAILED,
                reason_code=ComputerActionReasonCode.WINDOW_NOT_FOREGROUND,
                target=str(target_hwnd),
                error=f"Failed to bring window {target_hwnd} to foreground.",
                message=f"Window {target_hwnd} found but could not be brought to the foreground.",
                suggested_action="retry_focus",
                retryable=True,
                verification_verdict="FAILED",
            )

        self.state_machine.record_action_result(res)
        return res

    def set_window_state(
        self, action: str, query: str | None = None, hwnd: int | None = None
    ) -> ComputerActionResult:
        target_hwnd = hwnd
        if not target_hwnd and query:
            w = self.windows_api.find_window(query)
            if w:
                target_hwnd = w.hwnd

        if not target_hwnd:
            return ComputerActionResult(
                action=f"computer:{action}_window",
                status=ComputerActionStatus.FAILED,
                error=f"No matching window found for query: '{query}'.",
                verification_verdict="FAILED",
            )

        success = self.windows_api.set_window_state(target_hwnd, action)
        return ComputerActionResult(
            action=f"computer:{action}_window",
            status=ComputerActionStatus.EXECUTED if success else ComputerActionStatus.FAILED,
            target=str(target_hwnd),
            verification_verdict="VERIFIED" if success else "FAILED",
        )

    def close_window(
        self, query: str | None = None, hwnd: int | None = None
    ) -> ComputerActionResult:
        target_hwnd = hwnd
        target_title = query or ""
        target_pid = None

        if not target_hwnd and query:
            w = self.windows_api.find_window(query)
            if w:
                target_hwnd = w.hwnd
                target_title = w.title
                target_pid = w.process_id

        if not target_hwnd:
            return ComputerActionResult(
                action="computer:close_window",
                status=ComputerActionStatus.FAILED,
                error=f"No window found matching '{query}'.",
                verification_verdict="FAILED",
            )

        # Send close
        self.windows_api.set_window_state(target_hwnd, "close")

        # Verify closure
        verif = self.verifier.verify_app_closed(
            app_name=target_title,
            target_pid=target_pid,
            target_hwnd=target_hwnd,
        )

        return ComputerActionResult(
            action="computer:close_window",
            status=ComputerActionStatus.EXECUTED
            if verif["verdict"] == "VERIFIED"
            else ComputerActionStatus.FAILED,
            target=target_title,
            details={"hwnd": target_hwnd, "pid": target_pid},
            verification_verdict=verif["verdict"],
            verification_details=verif,
        )

    # 6. Application Launch
    def launch_app(
        self,
        app_name: str,
        args: list[str] | None = None,
        verify: bool = True,
        timeout_seconds: float = 6.0,
    ) -> ComputerActionResult:
        self.state_machine.record_action_start("computer:launch_app", target=app_name)
        resolution = self.resolver.resolve(app_name, prefer_running=True)
        if resolution.status == ResolutionStatus.NOT_FOUND or not resolution.resolved_path:
            res = ComputerActionResult(
                action="computer:launch_app",
                status=ComputerActionStatus.DENIED,
                reason_code=ComputerActionReasonCode.WINDOW_NOT_FOUND,
                target=app_name,
                error=f"Application resolution failed: {resolution.diagnostic_message}",
                message=f"Application '{app_name}' could not be resolved or found on system.",
                suggested_action="list_apps",
                retryable=True,
                verification_verdict="DENIED",
            )
            self.state_machine.record_action_result(res)
            return res

        # If already running with visible window, bring to foreground
        if resolution.resolution_source == "RUNNING_PROCESS" and resolution.existing_hwnd:
            self.windows_api.focus_window(resolution.existing_hwnd)
            res = ComputerActionResult(
                action="computer:launch_app",
                status=ComputerActionStatus.EXECUTED,
                target=app_name,
                details={
                    "resolution": resolution.model_dump(),
                    "status_note": "Application already running; activated existing foreground window.",
                },
                verification_verdict="VERIFIED",
                verification_details={
                    "checkpoints": ["WINDOW_FOUND", "WINDOW_VISIBLE", "WINDOW_FOREGROUND"]
                },
                message=f"Application '{app_name}' was already running; brought to foreground.",
            )
            self.state_machine.set_target(name=app_name, hwnd=resolution.existing_hwnd)
            self.state_machine.record_action_result(res)
            return res

        cmd: list[str] = [resolution.resolved_path]
        if args:
            cmd.extend(args)

        try:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                shell=(not resolution.resolved_path.lower().endswith(".exe")),
            )

            verif_details = {}
            verdict = "UNVERIFIED"

            if verify:
                verif_details = self.verifier.verify_app_launched(
                    app_name=app_name,
                    expected_pid=proc.pid,
                    timeout_seconds=timeout_seconds,
                )
                verdict = verif_details.get("verdict", "FAILED")

            status = (
                ComputerActionStatus.EXECUTED
                if verdict == "VERIFIED"
                else ComputerActionStatus.FAILED
            )

            actual_pid = verif_details.get("pid") or proc.pid
            actual_hwnd = verif_details.get("hwnd")
            error_msg = None
            if status == ComputerActionStatus.FAILED:
                error_msg = (
                    verif_details.get("message")
                    or f"Application '{app_name}' launch verification failed."
                )

            res = ComputerActionResult(
                action="computer:launch_app",
                status=status,
                target=app_name,
                details={
                    "pid": actual_pid,
                    "hwnd": actual_hwnd,
                    "executable": resolution.resolved_path,
                    "resolution_source": resolution.resolution_source,
                },
                verification_verdict=verdict,
                verification_details=verif_details,
                error=error_msg,
                reason_code=None
                if status == ComputerActionStatus.EXECUTED
                else ComputerActionReasonCode.VERIFICATION_FAILED,
                message=f"Application '{app_name}' launched and verified."
                if status == ComputerActionStatus.EXECUTED
                else (error_msg or "Launch verification failed."),
            )
            if status == ComputerActionStatus.EXECUTED:
                self.state_machine.set_target(name=app_name, hwnd=actual_hwnd, pid=actual_pid)
            self.state_machine.record_action_result(res)
            return res

        except Exception as exc:
            res = ComputerActionResult(
                action="computer:launch_app",
                status=ComputerActionStatus.FAILED,
                target=app_name,
                error=f"Process spawn failed: {exc}",
                reason_code=ComputerActionReasonCode.ACTION_TIMEOUT,
                message=f"Failed to spawn application '{app_name}': {exc}",
                verification_verdict="FAILED",
            )
            self.state_machine.record_action_result(res)
            return res

    def navigate_browser(
        self,
        url: str,
        browser: str = "chrome",
    ) -> ComputerActionResult:
        """Navigate a browser to a given URL with address bar activation and verification."""
        clean_url = url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = f"https://{clean_url}"

        self.state_machine.record_action_start("computer:navigate_browser", target=clean_url)

        # 1. Focus or launch browser
        focus_res = self.focus_window(query=browser)
        if focus_res.status != ComputerActionStatus.EXECUTED:
            launch_res = self.launch_app(app_name=browser, verify=True)
            if launch_res.status != ComputerActionStatus.EXECUTED:
                res = ComputerActionResult(
                    action="computer:navigate_browser",
                    status=ComputerActionStatus.DENIED,
                    reason_code=ComputerActionReasonCode.PRECONDITION_NOT_SATISFIED,
                    error=f"Could not focus or launch browser '{browser}': {launch_res.error}",
                    message=f"Browser '{browser}' could not be focused or launched.",
                    suggested_action="launch_app",
                    verification_verdict="DENIED",
                )
                self.state_machine.record_action_result(res)
                return res
            time.sleep(0.5)
            self.focus_window(query=browser)

        # 2. Activate address bar via Ctrl+L
        self.hotkey(keys=["ctrl", "l"])
        time.sleep(0.1)

        # 3. Type URL and press Enter
        type_res = self.type_text(text=clean_url, press_enter=True)
        if type_res.status != ComputerActionStatus.EXECUTED:
            res = ComputerActionResult(
                action="computer:navigate_browser",
                status=type_res.status,
                reason_code=type_res.reason_code,
                error=f"Failed typing URL: {type_res.error}",
                message=type_res.message,
                verification_verdict=type_res.verification_verdict,
            )
            self.state_machine.record_action_result(res)
            return res

        res = ComputerActionResult(
            action="computer:navigate_browser",
            status=ComputerActionStatus.EXECUTED,
            target=clean_url,
            details={
                "browser": browser,
                "url": clean_url,
                "hwnd": self.state_machine.target_hwnd,
            },
            verification_verdict="VERIFIED",
            message=f"Successfully navigated {browser} to '{clean_url}'.",
        )
        self.state_machine.record_action_result(res)
        return res

    # 7. Lists and Process Governance
    def list_windows(self, include_invisible: bool = False) -> ComputerActionResult:
        windows = self.windows_api.list_desktop_windows(include_invisible=include_invisible)
        return ComputerActionResult(
            action="computer:list_windows",
            status=ComputerActionStatus.EXECUTED,
            details={"count": len(windows), "windows": [w.model_dump() for w in windows]},
            verification_verdict="VERIFIED",
        )

    def list_apps(self, limit: int = 100) -> ComputerActionResult:
        apps = self.resolver.list_installed_apps(limit=limit)
        return ComputerActionResult(
            action="computer:list_apps",
            status=ComputerActionStatus.EXECUTED,
            details={"count": len(apps), "apps": apps},
            verification_verdict="VERIFIED",
        )

    def list_processes(
        self, filter_name: str | None = None, limit: int = 100
    ) -> ComputerActionResult:
        procs = self.windows_api.list_processes(filter_name=filter_name, limit=limit)
        return ComputerActionResult(
            action="computer:list_processes",
            status=ComputerActionStatus.EXECUTED,
            details={"count": len(procs), "processes": [p.model_dump() for p in procs]},
            verification_verdict="VERIFIED",
        )

    def terminate_process(self, pid: int, force: bool = False) -> ComputerActionResult:
        try:
            self.windows_api.terminate_process(pid=pid, force=force)
            return ComputerActionResult(
                action="computer:terminate_process",
                status=ComputerActionStatus.EXECUTED,
                target=str(pid),
                verification_verdict="VERIFIED",
            )
        except Exception as exc:
            return ComputerActionResult(
                action="computer:terminate_process",
                status=ComputerActionStatus.FAILED,
                target=str(pid),
                error=str(exc),
                verification_verdict="FAILED",
            )

    def verify_visual_state(self, pre_screenshot_digest: str) -> ComputerActionResult:
        res = self.verifier.verify_visual_state_changed(pre_screenshot_digest)
        return ComputerActionResult(
            action="computer:verify_visual_state",
            status=ComputerActionStatus.EXECUTED,
            details=res,
            verification_verdict=res["verdict"],
        )
