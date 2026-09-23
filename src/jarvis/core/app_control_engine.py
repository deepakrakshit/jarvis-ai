"""Universal Application Control Engine for Deep In-App and UI Automation.

Orchestrates multi-provider hierarchy:
1. Native Application / URI / Programmatic protocol
2. Microsoft Windows UI Automation (UIA) with control patterns
3. Browser DOM subsystem (Playwright)
4. Computer-Use coordinate and visual fallback (JARVIS CUA Substrate)

Executes closed-loop automation: Observe -> Act -> Verify.
"""

import time
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from jarvis.execution.computer.contract import (
    ActionResultEffect,
    ComputerActionName,
    ComputerActParams,
    ComputerActResult,
    ComputerObservation,
)
from jarvis.execution.substrate_bridge import SubstrateBridge, substrate_bridge
from jarvis.execution.windows.app_launcher import app_launcher
from jarvis.execution.windows.app_registry import app_registry
from jarvis.execution.windows.desktop import (
    capture_screenshot,
    mouse_click,
    press_key,
    type_text,
)
from jarvis.execution.windows.uia import UIElementInfo, windows_uia
from jarvis.execution.windows.window_manager import WindowInfo, window_manager
from jarvis.telemetry import logger


class ActionExecutionStatus(str, Enum):
    """Canonical verification status of an application action."""

    REQUESTED = "REQUESTED"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    AMBIGUOUS = "AMBIGUOUS"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


class ProviderType(str, Enum):
    """Execution provider selected by the control engine."""

    NATIVE_API = "native_api"
    WINDOWS_UIA = "windows_uia"
    BROWSER_DOM = "browser_dom"
    COMPUTER_FALLBACK = "computer_fallback"


@dataclass
class TargetResolutionResult:
    """Outcome of resolving a natural language target to an actionable control."""

    element: Optional[UIElementInfo]
    confidence: float
    provider: ProviderType
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    ambiguity_reason: Optional[str] = None


@dataclass
class AppActionResult:
    """Closed-loop verified execution result of an in-app control operation."""

    status: ActionExecutionStatus
    provider: ProviderType
    action: str
    target_window: Optional[str] = None
    target_element: Optional[str] = None
    confidence: float = 1.0
    verified: bool = False
    observation_before: Optional[Dict[str, Any]] = None
    observation_after: Optional[Dict[str, Any]] = None
    output: Any = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary representation."""
        res = asdict(self)
        res["status"] = self.status.value
        res["provider"] = self.provider.value
        return res


class AppControlEngine:
    """Universal Application and UI Control Engine."""

    def __init__(self, bridge: Optional[SubstrateBridge] = None) -> None:
        self.registry = app_registry
        self.launcher = app_launcher
        self.windows = window_manager
        self.uia = windows_uia
        self.substrate_bridge: SubstrateBridge = bridge or substrate_bridge

    def delegate_to_substrate(
        self, action: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Directly delegate computer action to JARVIS Node.js execution substrate."""
        return self.substrate_bridge.execute_act_sync(action, params)

    async def delegate_to_substrate_async(
        self, action: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Asynchronously delegate computer action to JARVIS Node.js execution substrate."""
        return await self.substrate_bridge.execute_act(action, params)

    def launch_application(
        self,
        target: str,
        arguments: Optional[List[str]] = None,
        timeout_seconds: float = 6.0,
    ) -> AppActionResult:
        """Launch an application asynchronously with verified window readiness."""
        logger.info(f"AppControlEngine: Launching application '{target}'...")

        obs_before = {
            "open_windows_count": len(self.windows.list_windows()),
            "timestamp": time.time(),
        }

        launch_res = self.launcher.launch(
            target=target,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
        )

        obs_after = {
            "spawned_pid": launch_res.process_id,
            "ready": launch_res.ready,
            "window_title": launch_res.window_title,
            "hwnd": launch_res.hwnd,
            "timestamp": time.time(),
        }

        status = (
            ActionExecutionStatus.SUCCESS if launch_res.success else ActionExecutionStatus.FAILED
        )

        return AppActionResult(
            status=status,
            provider=ProviderType.NATIVE_API,
            action="launch_application",
            target_window=launch_res.window_title or target,
            verified=launch_res.ready,
            observation_before=obs_before,
            observation_after=obs_after,
            output=launch_res.to_dict(),
            error=launch_res.error,
        )

    def focus_window(self, target: str) -> AppActionResult:
        """Bring a target application window to the foreground cleanly."""
        logger.info(f"AppControlEngine: Focusing window matching '{target}'...")
        win = self.windows.find_window(target)
        if not win:
            return AppActionResult(
                status=ActionExecutionStatus.FAILED,
                provider=ProviderType.WINDOWS_UIA,
                action="focus_window",
                target_window=target,
                verified=False,
                error=f"No window found matching '{target}'.",
            )

        success = self.windows.focus_window(win.hwnd)
        focused = self.windows.get_active_window()
        verified = focused is not None and (focused.hwnd == win.hwnd or win.title in focused.title)

        return AppActionResult(
            status=ActionExecutionStatus.SUCCESS if success else ActionExecutionStatus.FAILED,
            provider=ProviderType.WINDOWS_UIA,
            action="focus_window",
            target_window=win.title,
            verified=verified,
            output={"hwnd": win.hwnd, "title": win.title},
        )

    def close_window(self, target: str) -> AppActionResult:
        """Close an application window gracefully."""
        logger.info(f"AppControlEngine: Closing window matching '{target}'...")
        win = self.windows.find_window(target)
        if not win:
            return AppActionResult(
                status=ActionExecutionStatus.FAILED,
                provider=ProviderType.WINDOWS_UIA,
                action="close_window",
                target_window=target,
                verified=False,
                error=f"No window found matching '{target}'.",
            )

        success = self.windows.close_window(win.hwnd)
        time.sleep(0.3)
        still_open = self.windows.find_window_by_hwnd(win.hwnd)

        return AppActionResult(
            status=ActionExecutionStatus.SUCCESS if success else ActionExecutionStatus.FAILED,
            provider=ProviderType.WINDOWS_UIA,
            action="close_window",
            target_window=win.title,
            verified=still_open is None,
            output={"closed_hwnd": win.hwnd, "title": win.title},
        )

    def list_windows(self) -> List[Dict[str, Any]]:
        """List all open desktop windows."""
        windows = self.windows.list_windows()
        return [w.to_dict() for w in windows]

    def inspect_ui(
        self,
        window_target: Optional[str] = None,
        max_depth: int = 5,
        max_elements: int = 150,
    ) -> Dict[str, Any]:
        """Inspect the UI tree of the target window and return normalized controls."""
        target_win: Optional[WindowInfo] = None
        if window_target:
            target_win = self.windows.find_window(window_target)
            if not target_win:
                return {
                    "error": f"Target window '{window_target}' not found.",
                    "controls": [],
                    "summary": "",
                }
        else:
            target_win = self.windows.get_active_window()
            if not target_win:
                return {
                    "error": "No active window found to inspect.",
                    "controls": [],
                    "summary": "",
                }

        hwnd = target_win.hwnd
        controls = self.uia.inspect_window_tree(
            hwnd=hwnd,
            max_depth=max_depth,
            max_elements=max_elements,
            interactive_only=True,
        )
        summary = self.uia.generate_tree_summary(
            hwnd=hwnd,
            max_depth=max_depth,
            max_elements=max_elements,
        )

        return {
            "window": target_win.to_dict(),
            "controls_count": len(controls),
            "controls": [c.to_dict() for c in controls],
            "tree_summary": summary,
        }

    def resolve_target(
        self,
        hwnd: int,
        query: str,
        controls: Optional[List[UIElementInfo]] = None,
    ) -> TargetResolutionResult:
        """Resolve a natural language element query into a concrete UI control."""
        if controls is None:
            controls = self.uia.inspect_window_tree(
                hwnd=hwnd,
                max_depth=6,
                max_elements=200,
                interactive_only=True,
            )

        if not controls:
            return TargetResolutionResult(
                element=None,
                confidence=0.0,
                provider=ProviderType.COMPUTER_FALLBACK,
                ambiguity_reason="No interactive controls exposed by UI Automation.",
            )

        normalized_query = query.strip().lower()
        scored_candidates: List[Tuple[float, UIElementInfo]] = []

        for c in controls:
            name_norm = c.name.lower() if c.name else ""
            auto_id_norm = c.automation_id.lower() if c.automation_id else ""
            type_norm = c.control_type.lower() if c.control_type else ""

            # Exact AutomationId match
            if auto_id_norm and auto_id_norm == normalized_query:
                scored_candidates.append((1.0, c))
                continue

            # Exact Name match
            if name_norm and name_norm == normalized_query:
                scored_candidates.append((0.95, c))
                continue

            # Substring match on Name
            if name_norm and normalized_query in name_norm:
                scored_candidates.append((0.85, c))
                continue

            # Substring match on AutomationId
            if auto_id_norm and normalized_query in auto_id_norm:
                scored_candidates.append((0.80, c))
                continue

            # Composite query: e.g. "Save button", "File menu"
            if name_norm and type_norm and f"{name_norm} {type_norm}" == normalized_query:
                scored_candidates.append((0.90, c))
                continue

            # Fuzzy text ratio on Name
            if name_norm:
                ratio = SequenceMatcher(None, normalized_query, name_norm).ratio()
                if ratio >= 0.70:
                    scored_candidates.append((ratio * 0.80, c))
                    continue

            # Value or Help Text match
            if c.value and normalized_query in c.value.lower():
                scored_candidates.append((0.65, c))
                continue

        # Sort candidates descending by confidence
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        if not scored_candidates:
            return TargetResolutionResult(
                element=None,
                confidence=0.0,
                provider=ProviderType.COMPUTER_FALLBACK,
                ambiguity_reason=f"No elements matched query '{query}'.",
            )

        top_score, top_elem = scored_candidates[0]

        # Check for ambiguity if multiple high-confidence matches are tied
        candidates_summary = [
            {
                "score": round(score, 3),
                "name": elem.name,
                "type": elem.control_type,
                "automation_id": elem.automation_id,
            }
            for score, elem in scored_candidates[:5]
        ]

        if len(scored_candidates) > 1:
            second_score, _ = scored_candidates[1]
            if top_score < 0.85 and (top_score - second_score) < 0.05:
                return TargetResolutionResult(
                    element=top_elem,
                    confidence=top_score,
                    provider=ProviderType.WINDOWS_UIA,
                    candidates=candidates_summary,
                    ambiguity_reason="Multiple closely scoring elements found.",
                )

        return TargetResolutionResult(
            element=top_elem,
            confidence=top_score,
            provider=ProviderType.WINDOWS_UIA,
            candidates=candidates_summary,
        )

    def interact(
        self,
        window_target: str,
        element_query: str,
        action: str = "click",
        value: Optional[str] = None,
    ) -> AppActionResult:
        """Execute closed-loop interaction: Observe -> Act -> Verify."""
        logger.info(
            f"AppControlEngine: Interacting with '{element_query}' in '{window_target}' (action={action})..."
        )

        # 1. OBSERVE: Find window
        win = self.windows.find_window(window_target)
        if not win:
            return AppActionResult(
                status=ActionExecutionStatus.FAILED,
                provider=ProviderType.WINDOWS_UIA,
                action=action,
                target_window=window_target,
                target_element=element_query,
                verified=False,
                error=f"Window '{window_target}' not found.",
            )

        # Ensure window is focused
        self.windows.focus_window(win.hwnd)
        time.sleep(0.1)

        # 2. RESOLVE TARGET
        resolution = self.resolve_target(hwnd=win.hwnd, query=element_query)
        if not resolution.element or resolution.confidence < 0.60:
            logger.warning(
                f"UIA resolution failed or confidence too low ({resolution.confidence}). "
                f"Falling back to Computer-Use coordinate interaction."
            )
            return self._fallback_computer_interaction(
                win=win,
                query=element_query,
                action=action,
                value=value,
            )

        target_elem = resolution.element
        raw_elem = self.uia.find_element_by_name(
            hwnd=win.hwnd,
            name=target_elem.name,
            automation_id=target_elem.automation_id,
            control_type=target_elem.control_type,
        )

        obs_before = {
            "element": target_elem.to_dict(),
            "confidence": resolution.confidence,
            "timestamp": time.time(),
        }

        # 3. ACT: Invoke appropriate pattern based on requested action
        action_normalized = action.lower().strip()
        success = False
        action_err: Optional[str] = None

        try:
            if action_normalized in ("click", "invoke", "press"):
                success = self.uia.invoke_element(raw_elem)
            elif action_normalized in ("set_value", "type", "write"):
                if value is None:
                    action_err = "Value argument is required for set_value action."
                else:
                    success = self.uia.set_element_value(raw_elem, value=value)
            elif action_normalized == "toggle":
                success = self.uia.toggle_element(raw_elem)
            elif action_normalized == "select":
                success = self.uia.select_element(raw_elem)
            elif action_normalized == "expand":
                success = self.uia.expand_collapse_element(raw_elem, expand=True)
            elif action_normalized == "collapse":
                success = self.uia.expand_collapse_element(raw_elem, expand=False)
            elif action_normalized == "scroll":
                success = self.uia.scroll_element(raw_elem, direction="down")
            else:
                action_err = f"Unsupported action '{action}'."
        except Exception as err:
            action_err = str(err)
            logger.warning(f"Error executing action on UI element: {err}")

        if not success or action_err:
            logger.info(
                f"UIA action '{action_normalized}' unsuccessful (err={action_err}); "
                f"attempting coordinate fallback via JARVIS substrate..."
            )
            return self._fallback_computer_interaction(
                win=win,
                query=element_query,
                action=action,
                value=value,
                target_element=target_elem,
            )

        time.sleep(0.2)

        # 4. OBSERVE AGAIN & VERIFY
        obs_after: Dict[str, Any] = {
            "timestamp": time.time(),
            "action_executed": action_normalized,
            "raw_success": success,
        }

        verified = False
        if success and not action_err:
            # Re-read element to check for expected state change
            re_read = self.uia.find_element_by_name(
                hwnd=win.hwnd,
                name=target_elem.name,
                automation_id=target_elem.automation_id,
                control_type=target_elem.control_type,
            )
            if re_read:
                updated_info = self.uia._parse_element(re_read)
                if updated_info:
                    obs_after["updated_element"] = updated_info.to_dict()
                    if action_normalized in ("set_value", "type", "write") and value:
                        verified = updated_info.value == value or value in str(
                            updated_info.value or ""
                        )
                    else:
                        verified = True
            else:
                # Element might have disappeared after click (e.g. dialog dismissed or tab switched)
                verified = True
                obs_after["element_dismissed"] = True

        status = (
            ActionExecutionStatus.SUCCESS
            if (success and not action_err)
            else ActionExecutionStatus.FAILED
        )

        return AppActionResult(
            status=status,
            provider=ProviderType.WINDOWS_UIA,
            action=action_normalized,
            target_window=win.title,
            target_element=target_elem.name or target_elem.automation_id,
            confidence=resolution.confidence,
            verified=verified,
            observation_before=obs_before,
            observation_after=obs_after,
            output={"success": success, "target": target_elem.to_dict()},
            error=action_err,
        )

    def _fallback_computer_interaction(
        self,
        win: WindowInfo,
        query: str,
        action: str,
        value: Optional[str] = None,
        target_element: Optional[UIElementInfo] = None,
    ) -> AppActionResult:
        """Provider 4 fallback: Use coordinate mouse and keyboard automation via JARVIS substrate."""
        logger.info(f"Executing computer coordinate fallback for '{query}' in '{win.title}'")

        # Focus window
        self.windows.focus_window(win.hwnd)
        time.sleep(0.15)

        # If action is typing, type directly into focused window
        if action in ("type", "set_value", "write") and value:
            try:
                self.delegate_to_substrate("type", {"text": value})
            except Exception:
                type_text(value)
            return AppActionResult(
                status=ActionExecutionStatus.SUCCESS,
                provider=ProviderType.COMPUTER_FALLBACK,
                action=action,
                target_window=win.title,
                target_element=query,
                confidence=0.50,
                verified=False,
                output={"typed_chars": len(value)},
            )

        # If key press
        if action in ("key", "press_key") and value:
            try:
                self.delegate_to_substrate("key", {"key": value})
            except Exception:
                press_key(value)
            return AppActionResult(
                status=ActionExecutionStatus.SUCCESS,
                provider=ProviderType.COMPUTER_FALLBACK,
                action=action,
                target_window=win.title,
                target_element=query,
                confidence=0.50,
                verified=False,
                output={"key": value},
            )

        # Calculate click coordinates: prioritize target element bounding box if available
        if (
            target_element
            and target_element.width > 0
            and target_element.height > 0
            and target_element.left > 0
            and target_element.top > 0
        ):
            center_x = target_element.left + target_element.width // 2
            center_y = target_element.top + target_element.height // 2
            confidence = 0.70
        else:
            center_x = win.bounds["left"] + win.bounds["width"] // 2
            center_y = win.bounds["top"] + win.bounds["height"] // 2
            confidence = 0.40

        try:
            self.delegate_to_substrate("left_click", {"x": center_x, "y": center_y})
        except Exception:
            mouse_click(center_x, center_y)

        return AppActionResult(
            status=ActionExecutionStatus.SUCCESS,
            provider=ProviderType.COMPUTER_FALLBACK,
            action=action,
            target_window=win.title,
            target_element=query,
            confidence=confidence,
            verified=False,
            output={"fallback_click": {"x": center_x, "y": center_y}},
        )

    def execute_computer_action(self, params: ComputerActParams) -> ComputerActResult:
        """Execute canonical CUA computer action."""
        action_name = params.action.lower()
        logger.info(f"AppControlEngine: Executing computer action '{action_name}'...")

        obs = ComputerObservation()
        active_win = self.windows.get_active_window()
        if active_win:
            obs.active_window_title = active_win.title
            obs.active_window_hwnd = active_win.hwnd

        # Primary route: delegate to JARVIS Node.js execution substrate
        try:
            substrate_res = self.delegate_to_substrate(action_name, asdict(params))
            if substrate_res and substrate_res.get("ok"):
                return ComputerActResult(
                    ok=True,
                    effect=ActionResultEffect.CONFIRMED.value,
                    observation=obs,
                    details=substrate_res.get("details", substrate_res),
                )
        except Exception as substrate_err:
            logger.debug(f"Substrate delegation bypassed or falling back: {substrate_err}")

        try:
            if action_name == ComputerActionName.SCREENSHOT.value:
                shot = capture_screenshot()
                obs.screenshot_path = shot["artifact_path"]
                return ComputerActResult(
                    ok=True,
                    effect=ActionResultEffect.CONFIRMED.value,
                    observation=obs,
                    details=shot,
                )

            elif action_name in (
                ComputerActionName.LEFT_CLICK.value,
                ComputerActionName.RIGHT_CLICK.value,
                ComputerActionName.DOUBLE_CLICK.value,
                ComputerActionName.TRIPLE_CLICK.value,
            ):
                x = int(params.x) if params.x is not None else 0
                y = int(params.y) if params.y is not None else 0
                button = "right" if "right" in action_name else "left"
                clicks = 3 if "triple" in action_name else (2 if "double" in action_name else 1)
                res = mouse_click(x=x, y=y, button=button, clicks=clicks)
                return ComputerActResult(
                    ok=True,
                    effect=ActionResultEffect.CONFIRMED.value,
                    observation=obs,
                    details=res,
                )

            elif action_name == ComputerActionName.TYPE.value:
                text = params.text or ""
                res = type_text(text=text)
                return ComputerActResult(
                    ok=True,
                    effect=ActionResultEffect.CONFIRMED.value,
                    observation=obs,
                    details=res,
                )

            elif action_name == ComputerActionName.KEY.value:
                key_str = params.keys or "enter"
                res = press_key(key=key_str)
                return ComputerActResult(
                    ok=True,
                    effect=ActionResultEffect.CONFIRMED.value,
                    observation=obs,
                    details=res,
                )

            elif action_name == ComputerActionName.LIST_WINDOWS.value:
                wins = self.list_windows()
                return ComputerActResult(
                    ok=True,
                    effect=ActionResultEffect.CONFIRMED.value,
                    observation=obs,
                    details={"windows": wins, "count": len(wins)},
                )

            elif action_name == ComputerActionName.LAUNCH_APP.value:
                app_target = params.app or ""
                launch_res = self.launch_application(target=app_target)
                return ComputerActResult(
                    ok=launch_res.status == ActionExecutionStatus.SUCCESS,
                    effect=(
                        ActionResultEffect.CONFIRMED.value
                        if launch_res.verified
                        else ActionResultEffect.UNVERIFIABLE.value
                    ),
                    observation=obs,
                    details=launch_res.to_dict(),
                    error=launch_res.error,
                )

            elif action_name == ComputerActionName.BRING_TO_FRONT.value:
                win_target = params.window_ref or ""
                focus_res = self.focus_window(target=win_target)
                return ComputerActResult(
                    ok=focus_res.status == ActionExecutionStatus.SUCCESS,
                    effect=(
                        ActionResultEffect.CONFIRMED.value
                        if focus_res.verified
                        else ActionResultEffect.UNVERIFIABLE.value
                    ),
                    observation=obs,
                    details=focus_res.to_dict(),
                    error=focus_res.error,
                )

            elif action_name == ComputerActionName.KILL_APP.value:
                win_target = params.app or params.window_ref or ""
                close_res = self.close_window(target=win_target)
                return ComputerActResult(
                    ok=close_res.status == ActionExecutionStatus.SUCCESS,
                    effect=(
                        ActionResultEffect.CONFIRMED.value
                        if close_res.verified
                        else ActionResultEffect.UNVERIFIABLE.value
                    ),
                    observation=obs,
                    details=close_res.to_dict(),
                    error=close_res.error,
                )

            elif action_name == ComputerActionName.GET_ACCESSIBILITY_TREE.value:
                tree_win_target = params.window_ref
                tree_res = self.inspect_ui(
                    window_target=tree_win_target,
                    max_depth=params.depth or 5,
                    max_elements=params.max_elements or 150,
                )
                obs.elements_count = tree_res.get("controls_count", 0)
                obs.raw_tree = tree_res.get("tree_summary")
                return ComputerActResult(
                    ok="error" not in tree_res,
                    effect=ActionResultEffect.CONFIRMED.value,
                    observation=obs,
                    details=tree_res,
                )

            elif action_name == ComputerActionName.SET_VALUE.value:
                win_target = params.window_ref or ""
                elem_target = params.element_ref or ""
                val = params.value or ""
                interact_res = self.interact(
                    window_target=win_target,
                    element_query=elem_target,
                    action="set_value",
                    value=val,
                )
                return ComputerActResult(
                    ok=interact_res.status == ActionExecutionStatus.SUCCESS,
                    effect=(
                        ActionResultEffect.CONFIRMED.value
                        if interact_res.verified
                        else ActionResultEffect.UNVERIFIABLE.value
                    ),
                    observation=obs,
                    details=interact_res.to_dict(),
                    error=interact_res.error,
                )

            elif action_name == ComputerActionName.WAIT.value:
                duration_sec = (params.duration_ms or 500) / 1000.0
                time.sleep(duration_sec)
                return ComputerActResult(
                    ok=True,
                    effect=ActionResultEffect.CONFIRMED.value,
                    observation=obs,
                    details={"waited_seconds": duration_sec},
                )

            else:
                return ComputerActResult(
                    ok=False,
                    effect=ActionResultEffect.UNVERIFIABLE.value,
                    error=f"Unsupported computer action: '{action_name}'",
                )

        except Exception as err:
            logger.error(f"Error executing computer action '{action_name}': {err}")
            return ComputerActResult(
                ok=False,
                effect=ActionResultEffect.UNVERIFIABLE.value,
                observation=obs,
                error=str(err),
            )


# Global singleton instance
app_control_engine = AppControlEngine()
