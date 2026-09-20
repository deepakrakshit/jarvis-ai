"""Microsoft Windows UI Automation (UIA) Integration Engine.

Provides native accessibility and control-pattern automation for Windows applications
via COM UIAutomationCore.dll without relying on fragile pixel coordinate guessing.
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

try:
    import comtypes
    import comtypes.automation
    import comtypes.client
except ImportError:
    comtypes = None

from jarvis.execution.windows.winsta import ensure_interactive_desktop_attached
from jarvis.telemetry import logger

# Control type ID to human-readable string mapping
CONTROL_TYPE_NAMES: Dict[int, str] = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
}

# Reverse mapping for string-based control type queries
NAME_TO_CONTROL_TYPE: Dict[str, int] = {v.lower(): k for k, v in CONTROL_TYPE_NAMES.items()}


@dataclass
class UIElementInfo:
    """Structured description of an interactive Windows UI control."""

    automation_id: str
    name: str
    control_type: str
    control_type_id: int
    class_name: str
    is_enabled: bool
    is_offscreen: bool
    left: int
    top: int
    width: int
    height: int
    supported_patterns: List[str]
    value: Optional[str] = None
    help_text: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return serializable dictionary."""
        return asdict(self)


class WindowsUIAutomation:
    """Native Microsoft UI Automation client using COM interfaces."""

    def __init__(self) -> None:
        self._uia: Any = None
        self._client: Any = None
        self._initialized = False

    def _ensure_initialized(self) -> bool:
        """Lazy-initialize COM UIAutomationCore typelib and IUIAutomation engine."""
        if self._initialized and self._uia is not None:
            return True

        ensure_interactive_desktop_attached()

        try:
            import comtypes.client

            self._client = comtypes.client.GetModule("UIAutomationCore.dll")
            self._uia = comtypes.client.CreateObject(
                self._client.CUIAutomation,
                interface=self._client.IUIAutomation,
            )
            self._initialized = True
            logger.info("Windows UI Automation COM client initialized successfully.")
            return True
        except Exception as err:
            logger.warning(f"Failed to initialize Windows UI Automation: {err}")
            return False

    def get_element_from_hwnd(self, hwnd: int) -> Optional[Any]:
        """Retrieve native IUIAutomationElement for a given window handle."""
        if not self._ensure_initialized():
            return None

        try:
            return self._uia.ElementFromHandle(hwnd)
        except Exception as err:
            logger.debug(f"Could not get UIA element from HWND {hwnd}: {err}")
            return None

    def inspect_window_tree(
        self,
        hwnd: int,
        max_elements: int = 150,
        interactive_only: bool = True,
        max_depth: int = 5,
    ) -> List[UIElementInfo]:
        """Inspect and return a flattened list of controls inside the specified window.

        Adheres to official Microsoft UI Automation architectural standards:
        - Uses ControlViewCondition to filter out non-interactive layout artifacts at COM layer.
        - Avoids deep recursive raw-tree traversals that cause performance bottlenecks.
        """
        root_elem = self.get_element_from_hwnd(hwnd)
        if root_elem is None:
            return []

        results: List[UIElementInfo] = []
        visited = set()

        try:
            # Official UIA pattern: Control view provides the interactive user-facing controls
            condition = (
                self._uia.ControlViewCondition
                if interactive_only and hasattr(self._uia, "ControlViewCondition")
                else self._uia.CreateTrueCondition()
            )
            elements = root_elem.FindAll(self._client.TreeScope_Descendants, condition)
            count = min(elements.Length, max_elements * 2)

            for i in range(count):
                elem = elements.GetElement(i)
                info = self._parse_element(elem)
                if info is None:
                    continue

                if interactive_only:
                    is_interactive = info.control_type in (
                        "Button",
                        "Edit",
                        "CheckBox",
                        "RadioButton",
                        "ComboBox",
                        "TabItem",
                        "MenuItem",
                        "Hyperlink",
                        "SplitButton",
                        "Slider",
                        "ListItem",
                        "TreeItem",
                    ) or bool(info.name and info.control_type not in ("Pane", "Group", "Separator"))
                    if not is_interactive or info.is_offscreen:
                        continue

                # Deduplicate identical items
                key = (info.automation_id, info.name, info.control_type, info.left, info.top)
                if key not in visited:
                    visited.add(key)
                    results.append(info)
                    if len(results) >= max_elements:
                        break
        except Exception as err:
            logger.debug(f"Error inspecting UIA tree for HWND {hwnd}: {err}")

        return results

    def find_element(
        self,
        hwnd: int,
        name: Optional[str] = None,
        automation_id: Optional[str] = None,
        control_type: Optional[str] = None,
        class_name: Optional[str] = None,
    ) -> Optional[Any]:
        """Find the single best matching raw IUIAutomationElement within target window.

        Implements official two-pass resolution:
        1. Fast exact match via FindFirst using combined PropertyConditions.
        2. Fallback normalized/substring scan over ControlView elements if exact match misses.
        """
        root_elem = self.get_element_from_hwnd(hwnd)
        if root_elem is None:
            return None

        # Build condition for pass 1 (exact match)
        conditions = []
        if name:
            cond = self._uia.CreatePropertyCondition(self._client.UIA_NamePropertyId, str(name))
            conditions.append(cond)

        if automation_id:
            cond = self._uia.CreatePropertyCondition(
                self._client.UIA_AutomationIdPropertyId, str(automation_id)
            )
            conditions.append(cond)

        if control_type:
            type_id = NAME_TO_CONTROL_TYPE.get(control_type.lower())
            if type_id is not None:
                cond = self._uia.CreatePropertyCondition(
                    self._client.UIA_ControlTypePropertyId, type_id
                )
                conditions.append(cond)

        if class_name:
            cond = self._uia.CreatePropertyCondition(
                self._client.UIA_ClassNamePropertyId, str(class_name)
            )
            conditions.append(cond)

        try:
            if len(conditions) == 1:
                target_cond = conditions[0]
            elif len(conditions) > 1:
                target_cond = self._uia.CreateAndConditionFromArray(conditions)
            else:
                target_cond = self._uia.ControlViewCondition

            # Pass 1: exact match
            elem = root_elem.FindFirst(self._client.TreeScope_Descendants, target_cond)
            if elem is not None:
                return elem

            # Pass 2: If searching by name or automation_id and exact match missed,
            # execute a normalized case-insensitive scan over ControlView
            if name or automation_id:
                query_name = (name or "").strip().lower()
                query_id = (automation_id or "").strip().lower()
                control_cond = getattr(
                    self._uia, "ControlViewCondition", self._uia.CreateTrueCondition()
                )
                descendants = root_elem.FindAll(self._client.TreeScope_Descendants, control_cond)

                best_match: Optional[Any] = None
                best_priority = 0

                for i in range(min(descendants.Length, 300)):
                    candidate = descendants.GetElement(i)
                    try:
                        cand_name = str(candidate.CurrentName or "").strip().lower()
                        cand_id = str(candidate.CurrentAutomationId or "").strip().lower()

                        # Priority 3: Exact case-insensitive match
                        if (query_name and cand_name == query_name) or (
                            query_id and cand_id == query_id
                        ):
                            return candidate

                        # Priority 2: Substring match
                        if query_name and query_name in cand_name:
                            if best_priority < 2:
                                best_match = candidate
                                best_priority = 2
                        elif query_id and query_id in cand_id:
                            if best_priority < 2:
                                best_match = candidate
                                best_priority = 2
                        elif query_name and cand_name and cand_name in query_name:
                            if best_priority < 1:
                                best_match = candidate
                                best_priority = 1
                    except Exception:
                        continue

                if best_match is not None:
                    return best_match

            return None
        except Exception as err:
            logger.debug(f"Error in find_element: {err}")
            return None

    def find_element_by_name(
        self,
        hwnd: int,
        name: Optional[str] = None,
        automation_id: Optional[str] = None,
        control_type: Optional[str] = None,
        class_name: Optional[str] = None,
    ) -> Optional[Any]:
        """Alias for find_element."""
        return self.find_element(
            hwnd=hwnd,
            name=name,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
        )

    def invoke_element(self, element: Any) -> bool:
        """Trigger primary click/action on an element via InvokePattern, TogglePattern, or Select."""
        if element is None or not self._ensure_initialized():
            return False

        # 1. Try InvokePattern
        try:
            pattern_obj = element.GetCurrentPattern(self._client.UIA_InvokePatternId)
            if pattern_obj:
                invoke_pattern = pattern_obj.QueryInterface(self._client.IUIAutomationInvokePattern)
                invoke_pattern.Invoke()
                logger.info("Successfully executed InvokePattern.Invoke()")
                return True
        except Exception:
            pass

        # 2. Try TogglePattern (checkboxes, toggle switches)
        try:
            pattern_obj = element.GetCurrentPattern(self._client.UIA_TogglePatternId)
            if pattern_obj:
                toggle_pattern = pattern_obj.QueryInterface(self._client.IUIAutomationTogglePattern)
                toggle_pattern.Toggle()
                logger.info("Successfully executed TogglePattern.Toggle()")
                return True
        except Exception:
            pass

        # 3. Try SelectionItemPattern (tabs, list items, radio buttons)
        try:
            pattern_obj = element.GetCurrentPattern(self._client.UIA_SelectionItemPatternId)
            if pattern_obj:
                select_pattern = pattern_obj.QueryInterface(
                    self._client.IUIAutomationSelectionItemPattern
                )
                select_pattern.Select()
                logger.info("Successfully executed SelectionItemPattern.Select()")
                return True
        except Exception:
            pass

        # 4. Try ExpandCollapsePattern (comboboxes, dropdowns, tree items)
        try:
            pattern_obj = element.GetCurrentPattern(self._client.UIA_ExpandCollapsePatternId)
            if pattern_obj:
                ec_pattern = pattern_obj.QueryInterface(
                    self._client.IUIAutomationExpandCollapsePattern
                )
                ec_pattern.Expand()
                logger.info("Successfully executed ExpandCollapsePattern.Expand()")
                return True
        except Exception:
            pass

        # 5. Fallback to programmatic focus + simulated click via OpenClaw substrate
        try:
            element.SetFocus()
            rect = element.CurrentBoundingRectangle
            x = rect.left + (rect.right - rect.left) // 2
            y = rect.top + (rect.bottom - rect.top) // 2

            try:
                from jarvis.execution.substrate_bridge import substrate_bridge

                res = substrate_bridge.execute_act_sync("left_click", {"x": x, "y": y})
                if res and res.get("ok"):
                    logger.info(f"Fallback clicked at ({x}, {y}) via OpenClaw substrate")
                    return True
            except Exception as bridge_err:
                logger.debug(f"Substrate bridge click fallback deferred: {bridge_err}")

            from jarvis.execution.windows.desktop import mouse_click

            mouse_click(x, y)
            logger.info(f"Fallback clicked at ({x}, {y}) via native Win32 desktop")
            return True
        except Exception as err:
            logger.warning(f"All invocation strategies failed on element: {err}")
            return False

    def set_element_value(self, element: Any, value: str) -> bool:
        """Set text value directly on an element via ValuePattern or focus+type."""
        if element is None or not self._ensure_initialized():
            return False

        # 1. Try ValuePattern
        try:
            pattern_obj = element.GetCurrentPattern(self._client.UIA_ValuePatternId)
            if pattern_obj:
                val_pattern = pattern_obj.QueryInterface(self._client.IUIAutomationValuePattern)
                val_pattern.SetValue(value)
                logger.info(f"Successfully set value via ValuePattern.SetValue('{value}')")
                return True
        except Exception:
            pass

        # 2. Fallback to SetFocus + typing via OpenClaw substrate
        try:
            element.SetFocus()
            try:
                from jarvis.execution.substrate_bridge import substrate_bridge

                res = substrate_bridge.execute_act_sync("type", {"text": value})
                if res and res.get("ok"):
                    logger.info(f"Fallback typed value via OpenClaw substrate: '{value}'")
                    return True
            except Exception as bridge_err:
                logger.debug(f"Substrate bridge type fallback deferred: {bridge_err}")

            from jarvis.execution.windows.desktop import type_text

            type_text(value)
            logger.info(f"Fallback typed value via native Win32 desktop: '{value}'")
            return True
        except Exception as err:
            logger.warning(f"Failed to set value on element: {err}")
            return False

    def toggle_element(self, element: Any) -> bool:
        """Toggle a checkbox or switch element."""
        if element is None or not self._ensure_initialized():
            return False

        try:
            pattern_obj = element.GetCurrentPattern(self._client.UIA_TogglePatternId)
            if pattern_obj:
                toggle_pattern = pattern_obj.QueryInterface(self._client.IUIAutomationTogglePattern)
                toggle_pattern.Toggle()
                return True
        except Exception as err:
            logger.warning(f"Failed to toggle element: {err}")
        return False

    def select_element(self, element: Any) -> bool:
        """Select a tab, list item, or radio button element."""
        if element is None or not self._ensure_initialized():
            return False

        try:
            pattern_obj = element.GetCurrentPattern(self._client.UIA_SelectionItemPatternId)
            if pattern_obj:
                select_pattern = pattern_obj.QueryInterface(
                    self._client.IUIAutomationSelectionItemPattern
                )
                select_pattern.Select()
                return True
        except Exception as err:
            logger.warning(f"Failed to select element: {err}")
        return False

    def expand_collapse_element(self, element: Any, expand: bool = True) -> bool:
        """Expand or collapse a dropdown combobox or tree item."""
        if element is None or not self._ensure_initialized():
            return False

        try:
            pattern_obj = element.GetCurrentPattern(self._client.UIA_ExpandCollapsePatternId)
            if pattern_obj:
                ec_pattern = pattern_obj.QueryInterface(
                    self._client.IUIAutomationExpandCollapsePattern
                )
                if expand:
                    ec_pattern.Expand()
                else:
                    ec_pattern.Collapse()
                return True
        except Exception as err:
            logger.warning(f"Failed to expand/collapse element: {err}")
        return False

    def scroll_element(self, element: Any, direction: str = "down", amount: int = 1) -> bool:
        """Scroll element pane in specified direction."""
        if element is None or not self._ensure_initialized():
            return False

        try:
            pattern_obj = element.GetCurrentPattern(self._client.UIA_ScrollPatternId)
            if pattern_obj:
                scroll_pattern = pattern_obj.QueryInterface(self._client.IUIAutomationScrollPattern)
                # ScrollAmount: 0 = LargeDecrement, 1 = SmallDecrement, 2 = NoAmount, 3 = LargeIncrement, 4 = SmallIncrement
                if direction in ("down", "right"):
                    h_amount = 3 if direction == "right" else 2
                    v_amount = 3 if direction == "down" else 2
                else:
                    h_amount = 0 if direction == "left" else 2
                    v_amount = 0 if direction == "up" else 2
                scroll_pattern.Scroll(h_amount, v_amount)
                return True
        except Exception as err:
            logger.warning(f"Failed to scroll element: {err}")
        return False

    def get_normalized_tree_summary(self, hwnd: int, max_items: int = 40) -> str:
        """Produce a clean, compact normalized summary of visible interactive controls for LLM consumption."""
        controls = self.inspect_window_tree(hwnd, max_elements=max_items, interactive_only=True)
        if not controls:
            return "No accessible interactive UI controls detected in current application window."

        lines = [f"Interactive Controls (Total {len(controls)}):"]
        for c in controls:
            state_desc = "Enabled" if c.is_enabled else "Disabled"
            val_desc = f", Value='{c.value}'" if c.value else ""
            id_desc = f" [ID: {c.automation_id}]" if c.automation_id else ""
            lines.append(f'  • {c.control_type}: "{c.name}"{id_desc} ({state_desc}{val_desc})')

        return "\n".join(lines)

    def generate_tree_summary(
        self,
        hwnd: int,
        max_depth: int = 5,
        max_elements: int = 40,
    ) -> str:
        """Alias for get_normalized_tree_summary with max_depth parameter."""
        return self.get_normalized_tree_summary(hwnd=hwnd, max_items=max_elements)

    def _parse_element(self, elem: Any) -> Optional[UIElementInfo]:
        """Extract structured UIElementInfo from raw IUIAutomationElement."""
        try:
            type_id = elem.CurrentControlType
            type_name = CONTROL_TYPE_NAMES.get(type_id, "Unknown")
            name = str(elem.CurrentName or "").strip()
            auto_id = str(elem.CurrentAutomationId or "").strip()
            class_name = str(elem.CurrentClassName or "").strip()
            is_enabled = bool(elem.CurrentIsEnabled)
            is_offscreen = bool(elem.CurrentIsOffscreen)

            rect = elem.CurrentBoundingRectangle
            width = max(0, rect.right - rect.left)
            height = max(0, rect.bottom - rect.top)

            # Detect supported patterns
            patterns = []
            if elem.GetCurrentPattern(self._client.UIA_InvokePatternId):
                patterns.append("Invoke")
            if elem.GetCurrentPattern(self._client.UIA_ValuePatternId):
                patterns.append("Value")
            if elem.GetCurrentPattern(self._client.UIA_TogglePatternId):
                patterns.append("Toggle")
            if elem.GetCurrentPattern(self._client.UIA_SelectionItemPatternId):
                patterns.append("SelectionItem")
            if elem.GetCurrentPattern(self._client.UIA_ExpandCollapsePatternId):
                patterns.append("ExpandCollapse")
            if elem.GetCurrentPattern(self._client.UIA_ScrollPatternId):
                patterns.append("Scroll")

            # Extract value if available
            val = None
            if "Value" in patterns:
                try:
                    vp = elem.GetCurrentPattern(self._client.UIA_ValuePatternId).QueryInterface(
                        self._client.IUIAutomationValuePattern
                    )
                    val = vp.CurrentValue
                except Exception:
                    pass

            return UIElementInfo(
                automation_id=auto_id,
                name=name,
                control_type=type_name,
                control_type_id=type_id,
                class_name=class_name,
                is_enabled=is_enabled,
                is_offscreen=is_offscreen,
                left=rect.left,
                top=rect.top,
                width=width,
                height=height,
                supported_patterns=patterns,
                value=val,
            )
        except Exception:
            return None


# Global singleton UI Automation service
uia_service = WindowsUIAutomation()
windows_uia = uia_service
