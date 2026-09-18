"""Windows UI Automation (UIA) Engine for semantic control perception and interaction."""

from __future__ import annotations

import ctypes
from contextlib import suppress
from ctypes import wintypes
from typing import Any

from jarvis.computer.models import UIElementInfo
from jarvis.computer.windows_api import WindowsAPI
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Standard UI Automation Property IDs
UIA_BoundingRectanglePropertyId = 30001
UIA_ProcessIdPropertyId = 30020
UIA_ControlTypePropertyId = 30003
UIA_LocalizedControlTypePropertyId = 30004
UIA_NamePropertyId = 30005
UIA_AutomationIdPropertyId = 30011
UIA_ClassNamePropertyId = 30012
UIA_IsEnabledPropertyId = 30010
UIA_IsOffscreenPropertyId = 30022
UIA_IsKeyboardFocusablePropertyId = 30009
UIA_HasKeyboardFocusPropertyId = 30008

# Map UIA Control Type IDs to friendly names
UIA_CONTROL_TYPE_NAMES: dict[int, str] = {
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


class UIAEngine:
    """Performs semantic UI element discovery, inspection, and pattern invocation."""

    def __init__(self, windows_api: WindowsAPI | None = None) -> None:
        self.windows_api = windows_api or WindowsAPI()
        self._uia: Any = None
        self._oleaut32: Any = None
        self._has_pywinauto = False

        if self.windows_api.is_windows:
            try:
                import pywinauto  # noqa: F401

                self._has_pywinauto = True
            except Exception:
                self._has_pywinauto = False

            try:
                self._uia = ctypes.windll.UIAutomationCore
                self._oleaut32 = ctypes.windll.oleaut32
            except Exception:
                pass

    def inspect_window_elements(
        self,
        hwnd: int,
        max_elements: int = 150,
    ) -> list[UIElementInfo]:
        """Discover semantic UI elements within a target window using UI Automation."""
        if not self.windows_api.is_windows:
            return []

        self.windows_api.attach_to_default_desktop()

        elements: list[UIElementInfo] = []

        # Strategy 1: Modern pywinauto UI Automation deep tree enumeration
        if self._has_pywinauto:
            try:
                from pywinauto.application import Application

                app = Application(backend="uia").connect(handle=hwnd)
                win = app.window(handle=hwnd)
                descendants = win.descendants()

                for d in descendants:
                    if len(elements) >= max_elements:
                        break

                    try:
                        info = d.element_info
                        rect = d.rectangle()
                        w = max(0, rect.right - rect.left)
                        h = max(0, rect.bottom - rect.top)

                        # Skip completely empty zero-size hidden elements
                        if w == 0 and h == 0:
                            continue

                        # Extract ClickablePoint if exposed by UIA
                        clickable_pt: tuple[int, int] | None = None
                        try:
                            elem = info.element
                            pt, has_pt = elem.GetClickablePoint()
                            if has_pt:
                                clickable_pt = (int(pt.x), int(pt.y))
                        except Exception:
                            clickable_pt = None

                        # Extract supported control patterns
                        patterns: list[str] = []
                        for pat_name in ("invoke", "set_text", "toggle", "select", "scroll"):
                            if hasattr(d, pat_name):
                                patterns.append(pat_name)

                        elements.append(
                            UIElementInfo(
                                element_id=f"uia_{hwnd}_{info.automation_id or len(elements)}",
                                name=info.name or "",
                                control_type=info.control_type or "Custom",
                                automation_id=str(info.automation_id or ""),
                                class_name=str(info.class_name or ""),
                                left=rect.left,
                                top=rect.top,
                                width=w,
                                height=h,
                                is_enabled=bool(info.enabled),
                                is_offscreen=bool(getattr(info, "offscreen", False)),
                                is_keyboard_focusable=bool(
                                    getattr(info, "keyboard_focusable", False)
                                ),
                                has_keyboard_focus=bool(getattr(info, "has_keyboard_focus", False)),
                                process_id=info.process_id or 0,
                                center_x=rect.left + w // 2,
                                center_y=rect.top + h // 2,
                                clickable_point=clickable_pt,
                                supported_patterns=patterns,
                            )
                        )
                    except Exception:
                        continue

                if elements:
                    return elements[:max_elements]

            except Exception as exc:
                logger.debug("pywinauto_uia_inspect_fallback", hwnd=hwnd, error=str(exc))

        # Strategy 2: C-Types UIAutomationCore node inspection
        root_node = self._get_uia_node(hwnd)
        if root_node:
            root_info = self._extract_node_info(root_node, hwnd)
            if root_info:
                elements.append(root_info)
            self._release_node(root_node)

        # Strategy 3: Win32 child window control enumeration fallback
        child_elements = self._enumerate_child_controls(
            hwnd, max_elements=max_elements - len(elements)
        )
        elements.extend(child_elements)

        return elements[:max_elements]

    def element_from_point(self, px: int, py: int) -> UIElementInfo | None:
        """Query the authoritative UI Automation element residing at physical coordinates (px, py)."""
        if not self.windows_api.is_windows:
            return None

        self.windows_api.attach_to_default_desktop()

        if self._has_pywinauto:
            try:
                from pywinauto.uia_defines import IUIA
                from pywinauto.uia_element_info import UIAElementInfo as PywinElementInfo

                pt = wintypes.POINT(int(px), int(py))
                raw_elem = IUIA().iuia.ElementFromPoint(pt)
                if raw_elem:
                    info = PywinElementInfo(raw_elem)
                    rect = info.rectangle
                    w = max(0, rect.right - rect.left)
                    h = max(0, rect.bottom - rect.top)

                    clickable_pt: tuple[int, int] | None = None
                    try:
                        c_pt, has_pt = raw_elem.GetClickablePoint()
                        if has_pt:
                            clickable_pt = (int(c_pt.x), int(c_pt.y))
                    except Exception:
                        pass

                    return UIElementInfo(
                        element_id=f"pt_{px}_{py}_{info.automation_id or 'auto'}",
                        name=info.name or "",
                        control_type=info.control_type or "Custom",
                        automation_id=str(info.automation_id or ""),
                        class_name=str(info.class_name or ""),
                        left=rect.left,
                        top=rect.top,
                        width=w,
                        height=h,
                        is_enabled=bool(info.enabled),
                        is_offscreen=bool(getattr(info, "offscreen", False)),
                        process_id=info.process_id or 0,
                        center_x=rect.left + w // 2,
                        center_y=rect.top + h // 2,
                        clickable_point=clickable_pt,
                    )
            except Exception as exc:
                logger.debug("element_from_point_failed", x=px, y=py, error=str(exc))

        # C-Types fallback: WindowFromPoint
        try:
            pt_ctypes = wintypes.POINT(int(px), int(py))
            target_hwnd = self.windows_api._user32.WindowFromPoint(pt_ctypes)
            if target_hwnd:
                rect = wintypes.RECT()
                self.windows_api._user32.GetWindowRect(target_hwnd, ctypes.byref(rect))
                w = max(0, rect.right - rect.left)
                h = max(0, rect.bottom - rect.top)
                return UIElementInfo(
                    element_id=f"win32_{target_hwnd}",
                    name="",
                    control_type="Window",
                    automation_id="",
                    class_name="",
                    left=rect.left,
                    top=rect.top,
                    width=w,
                    height=h,
                    is_enabled=True,
                    is_offscreen=False,
                    center_x=rect.left + w // 2,
                    center_y=rect.top + h // 2,
                )
        except Exception:
            pass

        return None

    def invoke_element_pattern(
        self,
        hwnd: int,
        automation_id: str = "",
        name: str = "",
    ) -> bool:
        """Invoke a button or actionable element semantically using UIA InvokePattern."""
        if not self.windows_api.is_windows or not self._has_pywinauto:
            return False

        self.windows_api.attach_to_default_desktop()

        try:
            from pywinauto.application import Application

            app = Application(backend="uia").connect(handle=hwnd)
            win = app.window(handle=hwnd)
            for d in win.descendants():
                info = d.element_info
                if (
                    (automation_id and info.automation_id == automation_id)
                    or (name and info.name == name)
                ) and hasattr(d, "invoke"):
                    d.invoke()
                    return True
        except Exception as exc:
            logger.warning("invoke_element_pattern_failed", hwnd=hwnd, error=str(exc))

        return False

    def set_element_value_pattern(
        self,
        hwnd: int,
        value: str,
        automation_id: str = "",
        name: str = "",
    ) -> bool:
        """Set edit field value semantically using UIA ValuePattern."""
        if not self.windows_api.is_windows or not self._has_pywinauto:
            return False

        self.windows_api.attach_to_default_desktop()

        try:
            from pywinauto.application import Application

            app = Application(backend="uia").connect(handle=hwnd)
            win = app.window(handle=hwnd)
            for d in win.descendants():
                info = d.element_info
                if (
                    (automation_id and info.automation_id == automation_id)
                    or (name and info.name == name)
                ) and hasattr(d, "set_text"):
                    d.set_text(value)
                    return True
        except Exception as exc:
            logger.warning("set_element_value_pattern_failed", hwnd=hwnd, error=str(exc))

        return False

    def find_elements_by_query(
        self,
        hwnd: int,
        query: str,
        control_type: str | None = None,
    ) -> list[UIElementInfo]:
        """Search for controls inside window matching accessible name or control type."""
        all_elems = self.inspect_window_elements(hwnd, max_elements=150)
        q_lower = query.strip().lower()

        matches: list[UIElementInfo] = []
        for el in all_elems:
            if control_type and control_type.lower() not in el.control_type.lower():
                continue

            if (
                q_lower in el.name.lower()
                or q_lower in el.automation_id.lower()
                or q_lower in el.class_name.lower()
            ):
                matches.append(el)

        return matches

    def _get_uia_node(self, hwnd: int) -> int | None:
        """Obtain HUIANODE from window handle."""
        if not self._uia:
            return None
        try:
            node = ctypes.c_void_p()
            hr = self._uia.UiaNodeFromHandle(hwnd, ctypes.byref(node))
            if hr == 0 and node.value:
                return node.value
        except Exception:
            pass
        return None

    def _release_node(self, node_val: int) -> None:
        """Release HUIANODE pointer."""
        if self._uia and hasattr(self._uia, "UiaNodeRelease"):
            with suppress(Exception):
                self._uia.UiaNodeRelease(ctypes.c_void_p(node_val))

    def _get_node_property(self, node_val: int, prop_id: int) -> Any:
        """Extract a VARIANT property value from a UIA node."""
        if not self._uia:
            return None

        variant_buf = ctypes.create_string_buffer(24)
        try:
            hr = self._uia.UiaGetPropertyValue(ctypes.c_void_p(node_val), prop_id, variant_buf)
            if hr != 0:
                return None

            raw_bytes = bytes(variant_buf.raw)
            vt = int.from_bytes(raw_bytes[:2], "little")

            if vt == 8:
                bstr_ptr = int.from_bytes(raw_bytes[8:16], "little")
                if bstr_ptr:
                    return ctypes.wstring_at(bstr_ptr)

            if vt == 3:
                return int.from_bytes(raw_bytes[8:12], "little", signed=True)

            if vt == 11:
                val = int.from_bytes(raw_bytes[8:10], "little", signed=True)
                return bool(val != 0)

            if vt == 5:
                import struct

                return struct.unpack("d", raw_bytes[8:16])[0]

        except Exception:
            pass
        return None

    def _extract_node_info(self, node_val: int, hwnd: int) -> UIElementInfo | None:
        """Read core accessibility properties from a UIA node."""
        try:
            name = self._get_node_property(node_val, UIA_NamePropertyId) or ""
            ctype_id = self._get_node_property(node_val, UIA_ControlTypePropertyId) or 50032
            auto_id = self._get_node_property(node_val, UIA_AutomationIdPropertyId) or ""
            cls_name = self._get_node_property(node_val, UIA_ClassNamePropertyId) or ""
            is_enabled = bool(
                self._get_node_property(node_val, UIA_IsEnabledPropertyId) is not False
            )
            is_offscreen = bool(
                self._get_node_property(node_val, UIA_IsOffscreenPropertyId) is True
            )
            is_focusable = bool(
                self._get_node_property(node_val, UIA_IsKeyboardFocusablePropertyId) is True
            )
            has_focus = bool(
                self._get_node_property(node_val, UIA_HasKeyboardFocusPropertyId) is True
            )

            rect = wintypes.RECT()
            self.windows_api._user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = max(0, rect.right - rect.left)
            h = max(0, rect.bottom - rect.top)

            ctype_str = UIA_CONTROL_TYPE_NAMES.get(ctype_id, "Custom")

            return UIElementInfo(
                element_id=f"uia_{hwnd}_{auto_id or 'root'}",
                name=name,
                control_type=ctype_str,
                automation_id=str(auto_id),
                class_name=str(cls_name),
                left=rect.left,
                top=rect.top,
                width=w,
                height=h,
                is_enabled=is_enabled,
                is_offscreen=is_offscreen,
                is_keyboard_focusable=is_focusable,
                has_keyboard_focus=has_focus,
                center_x=rect.left + w // 2,
                center_y=rect.top + h // 2,
            )
        except Exception:
            return None

    def _enumerate_child_controls(
        self, parent_hwnd: int, max_elements: int = 50
    ) -> list[UIElementInfo]:
        """Enumerate Win32 child controls within a parent window."""
        results: list[UIElementInfo] = []
        user32 = self.windows_api._user32
        if not user32:
            return results

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _child_cb(child_hwnd: int, lparam: Any) -> bool:
            if not child_hwnd:
                return True

            if not user32.IsWindowVisible(child_hwnd):
                return True

            length = user32.GetWindowTextLengthW(child_hwnd)
            title = ""
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(child_hwnd, buff, length + 1)
                title = buff.value.strip()

            cls_buff = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child_hwnd, cls_buff, 256)
            cls_name = cls_buff.value.strip()

            rect = wintypes.RECT()
            user32.GetWindowRect(child_hwnd, ctypes.byref(rect))
            w = max(0, rect.right - rect.left)
            h = max(0, rect.bottom - rect.top)

            if w < 5 or h < 5:
                return True

            cls_lower = cls_name.lower()
            if "button" in cls_lower:
                ctype = "Button"
            elif "edit" in cls_lower or "richedit" in cls_lower:
                ctype = "Edit"
            elif "combobox" in cls_lower:
                ctype = "ComboBox"
            elif "list" in cls_lower:
                ctype = "List"
            elif "static" in cls_lower:
                ctype = "Text"
            elif "tab" in cls_lower:
                ctype = "Tab"
            elif "menu" in cls_lower:
                ctype = "Menu"
            else:
                ctype = "Custom"

            results.append(
                UIElementInfo(
                    element_id=f"win32_{child_hwnd}",
                    name=title,
                    control_type=ctype,
                    automation_id="",
                    class_name=cls_name,
                    left=rect.left,
                    top=rect.top,
                    width=w,
                    height=h,
                    is_enabled=bool(user32.IsWindowEnabled(child_hwnd)),
                    is_offscreen=False,
                    center_x=rect.left + w // 2,
                    center_y=rect.top + h // 2,
                )
            )
            return len(results) < max_elements

        with suppress(Exception):
            user32.EnumChildWindows(parent_hwnd, WNDENUMPROC(_child_cb), 0)

        return results
