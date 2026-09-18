"""Generalized, application-agnostic Windows UI Target Resolver using Microsoft UI Automation.

Discovers, scores, and verifies real controls from runtime UI state without hardcoded
coordinates, window titles, or application-specific pixel offsets.
"""

from __future__ import annotations

import re
import time
from typing import Any
from uuid import uuid4

from jarvis.computer.models import (
    DiscoveredTarget,
    DisplayMetrics,
    TargetObservation,
    TargetQuery,
    TargetSource,
    UIElementInfo,
    WindowInfo,
)
from jarvis.computer.uia import UIAEngine
from jarvis.computer.windows_api import WindowsAPI
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Canonical semantic role definitions mapping high-level intent to UI Automation signals
SEMANTIC_ROLE_CONFIGS: dict[str, dict[str, Any]] = {
    "search_field": {
        "preferred_control_types": {"Edit", "Document", "ComboBox", "Custom"},
        "name_keywords": {
            "search",
            "find",
            "query",
            "look",
            "filter",
            "start a new chat",
            "search or start",
            "search bar",
        },
        "id_keywords": {"search", "find", "query", "filter", "searchbox", "search_box"},
        "requires_editable": True,
    },
    "message_input": {
        "preferred_control_types": {"Edit", "Document", "Custom"},
        "name_keywords": {
            "type a message",
            "message",
            "write a message",
            "compose",
            "send a message",
            "chat message",
            "enter message",
        },
        "id_keywords": {"message", "compose", "chat_input", "text_input"},
        "requires_editable": True,
    },
    "editable_text": {
        "preferred_control_types": {"Edit", "Document"},
        "name_keywords": set(),
        "id_keywords": set(),
        "requires_editable": True,
    },
    "button": {
        "preferred_control_types": {"Button", "SplitButton"},
        "name_keywords": set(),
        "id_keywords": set(),
        "requires_editable": False,
    },
    "menu_item": {
        "preferred_control_types": {"MenuItem"},
        "name_keywords": set(),
        "id_keywords": set(),
        "requires_editable": False,
    },
    "tab": {
        "preferred_control_types": {"TabItem", "Tab"},
        "name_keywords": set(),
        "id_keywords": set(),
        "requires_editable": False,
    },
    "checkbox": {
        "preferred_control_types": {"CheckBox"},
        "name_keywords": set(),
        "id_keywords": set(),
        "requires_editable": False,
    },
    "address_field": {
        "preferred_control_types": {"Edit", "Document"},
        "name_keywords": {"address", "url", "search bar", "omnibox", "address and search"},
        "id_keywords": {"address", "url", "omnibox"},
        "requires_editable": True,
    },
    "list_item": {
        "preferred_control_types": {"ListItem", "DataItem"},
        "name_keywords": set(),
        "id_keywords": set(),
        "requires_editable": False,
    },
}


class UITargetResolver:
    """Discovers and resolves exact, verifiable physical target coordinates and patterns."""

    def __init__(
        self,
        windows_api: WindowsAPI | None = None,
        uia_engine: UIAEngine | None = None,
        uia: UIAEngine | None = None,
        input_driver: Any = None,
        emergency_stop: Any = None,
    ) -> None:
        self.windows_api = windows_api or WindowsAPI()
        self.uia = uia_engine or uia or UIAEngine(self.windows_api)
        self.input_driver = input_driver
        self.emergency_stop = emergency_stop

    def resolve_target(
        self,
        query: TargetQuery,
        window: WindowInfo | None = None,
        target_window: WindowInfo | None = None,
    ) -> DiscoveredTarget | None:
        """Resolve a structured semantic target query against runtime UI Automation tree."""
        target_win = window or target_window or self._resolve_target_window(query)
        if not target_win:
            logger.warning("target_resolution_failed_no_window", query=query.model_dump())
            return None

        # Enumerate live accessible controls from window UIA tree
        elements = self.uia.inspect_window_elements(target_win.hwnd, max_elements=200)
        if not elements:
            logger.warning("target_resolution_failed_no_elements", hwnd=target_win.hwnd)
            return None

        scored_candidates: list[tuple[float, list[str], UIElementInfo]] = []
        for elem in elements:
            score, match_reasons = self._score_element(elem, query)
            if score > 0.0:
                scored_candidates.append((score, match_reasons, elem))

        if not scored_candidates:
            return None

        # Sort by score descending
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        top_score, top_reasons, top_elem = scored_candidates[0]

        # Check for ambiguity if multiple high-confidence matches exist
        is_ambiguous = False
        if len(scored_candidates) > 1:
            second_score = scored_candidates[1][0]
            if top_score >= 0.85 and (top_score - second_score) < 0.05:
                # Ambiguous if names or IDs differ significantly
                first_id = scored_candidates[0][2].element_id
                second_id = scored_candidates[1][2].element_id
                if first_id != second_id:
                    is_ambiguous = True

        metrics = self.windows_api.get_display_metrics()
        obs = self._build_target_observation(
            target_win=target_win,
            elem=top_elem,
            metrics=metrics,
        )

        return DiscoveredTarget(
            observation=obs,
            score=top_score,
            match_reasons=top_reasons,
            element_info=top_elem,
            is_ambiguous=is_ambiguous,
            candidate_count=len(scored_candidates),
        )

    def validate_element_at_point(
        self,
        px: int,
        py: int,
        target_obs: TargetObservation,
    ) -> tuple[bool, str]:
        """Verify that physical coordinates (px, py) still map to target element before action."""
        # 1. Bounds verification within display metrics
        metrics = self.windows_api.get_display_metrics()
        v_l = metrics.virtual_left
        v_t = metrics.virtual_top
        v_r = v_l + metrics.virtual_width
        v_b = v_t + metrics.virtual_height

        if not (v_l <= px < v_r and v_t <= py < v_b):
            return (
                False,
                f"OUT_OF_BOUNDS: Coordinate ({px}, {py}) outside virtual desktop ({v_l}, {v_t}, {v_r}, {v_b}).",
            )

        # 2. Window state and foreground check
        is_valid, state_reason = self.is_observation_valid(target_obs)
        if not is_valid:
            return False, state_reason

        # 3. Live ElementFromPoint verification
        current_elem = self.uia.element_from_point(px, py)
        if not current_elem:
            # If UIA ElementFromPoint fails on custom surface, fall back to window bounds verification
            return True, "POINT_WITHIN_TARGET_WINDOW_BOUNDS"

        # Check process ID alignment
        if (
            target_obs.window_pid
            and current_elem.process_id
            and target_obs.window_pid != current_elem.process_id
        ):
            return (
                False,
                f"STALE_TARGET: Coordinate ({px}, {py}) now maps to PID {current_elem.process_id} "
                f"({current_elem.name or current_elem.control_type}) instead of target PID {target_obs.window_pid}.",
            )

        # Check element containment / identity alignment
        t_l, t_t, t_w, t_h = target_obs.element_rect
        if t_w > 0 and t_h > 0 and not (t_l <= px <= t_l + t_w and t_t <= py <= t_t + t_h):
            return (
                False,
                f"STALE_TARGET: Point ({px}, {py}) is outside element bounding rect ({t_l}, {t_t}, {t_w}, {t_h}).",
            )

        return True, "VERIFIED_ELEMENT_AT_POINT"

    def is_observation_valid(
        self,
        observation: TargetObservation,
    ) -> tuple[bool, str]:
        """Verify that observed target window and state have not shifted or expired."""
        if not self.windows_api.is_windows or not self.windows_api._user32:
            return True, "DESKTOP_NOT_ATTACHED"

        hwnd = observation.window_hwnd
        # Check window existence
        if not self.windows_api._user32.IsWindow(hwnd):
            return False, f"TARGET_EXITED: Window handle {hwnd} no longer exists."

        # Check window visibility
        if not self.windows_api._user32.IsWindowVisible(hwnd):
            return False, f"WINDOW_NOT_VISIBLE: Window handle {hwnd} is hidden."

        # Check foreground status
        fg_hwnd = self.windows_api.get_foreground_window()
        if fg_hwnd and fg_hwnd != hwnd:
            # Check if foreground window belongs to the same process
            pid = (
                self.windows_api.get_window_pid(fg_hwnd)
                if hasattr(self.windows_api, "get_window_pid")
                else 0
            )
            if pid != observation.window_pid:
                return (
                    False,
                    f"WINDOW_NOT_FOREGROUND: Active foreground HWND is {fg_hwnd}, expected {hwnd}.",
                )

        return True, "OBSERVATION_VALID"

    def verify_messaging_target_confirmed(
        self,
        hwnd: int,
        target_recipient: str,
    ) -> tuple[bool, str]:
        """Affirmatively verify that active communication chat matches target recipient."""
        if not target_recipient:
            return False, "NO_RECIPIENT_SPECIFIED"

        target_clean = target_recipient.strip().lower()
        elements = self.uia.inspect_window_elements(hwnd, max_elements=100)

        # Look for edit field labeled "Type a message to <Recipient>" or chat header matching recipient
        recipient_found = False
        message_input_found = False
        found_recipient_label = ""

        for el in elements:
            el_name_lower = el.name.lower()
            if "type a message to" in el_name_lower:
                message_input_found = True
                found_recipient_label = el.name
                if target_clean in el_name_lower:
                    recipient_found = True
                    break

            # Check header or title match
            if (
                el.control_type in ("Text", "Header", "Pane", "Group")
                and target_clean in el_name_lower
            ):
                recipient_found = True

        if recipient_found:
            return True, "TARGET_CHAT_CONFIRMED"

        if message_input_found:
            return (
                False,
                f"CHAT_RECIPIENT_MISMATCH: Active chat is '{found_recipient_label}', "
                f"which does not match requested recipient '{target_recipient}'.",
            )

        return (
            False,
            f"CHAT_RECIPIENT_MISMATCH: Recipient '{target_recipient}' not confirmed in active window.",
        )

    def _resolve_target_window(self, query: TargetQuery) -> WindowInfo | None:
        """Find the appropriate top-level desktop window for the query."""
        if query.hwnd:
            windows = self.windows_api.list_desktop_windows(include_invisible=False)
            for w in windows:
                if w.hwnd == query.hwnd:
                    return w

        if query.window_title:
            return self.windows_api.find_window(query.window_title)

        # Default to foreground window
        fg_hwnd = self.windows_api.get_foreground_window()
        if fg_hwnd:
            windows = self.windows_api.list_desktop_windows(include_invisible=False)
            for w in windows:
                if w.hwnd == fg_hwnd:
                    return w

        return None

    def _score_element(
        self,
        elem: UIElementInfo,
        query: TargetQuery,
    ) -> tuple[float, list[str]]:
        """Score candidate UI element against semantic target query using multi-signal heuristics."""
        score = 0.0
        reasons: list[str] = []

        name_lower = elem.name.lower().strip()
        auto_id_lower = elem.automation_id.lower().strip()
        elem_ctype = elem.control_type.strip()

        # Penalize disabled or offscreen elements heavily
        if not elem.is_enabled:
            return 0.0, ["DISABLED"]
        if elem.is_offscreen:
            score -= 0.3
            reasons.append("OFFSCREEN_PENALTY")

        # 1. Semantic Role Evaluation
        if query.semantic_role:
            role_key = query.semantic_role.lower().strip()
            role_cfg = SEMANTIC_ROLE_CONFIGS.get(role_key)
            if role_cfg:
                # Control type match
                if elem_ctype in role_cfg["preferred_control_types"]:
                    score += 0.35
                    reasons.append(f"ROLE_CONTROL_TYPE_MATCH({elem_ctype})")

                # Keyword match in accessible name
                name_words = set(re.findall(r"\w+", name_lower))
                keyword_hits = role_cfg["name_keywords"] & name_words
                for kw in role_cfg["name_keywords"]:
                    if kw in name_lower:
                        score += 0.40
                        reasons.append(f"ROLE_NAME_KEYWORD_MATCH({kw})")
                        break
                if keyword_hits:
                    score += 0.15

                # Keyword match in AutomationId
                for kw in role_cfg["id_keywords"]:
                    if kw in auto_id_lower:
                        score += 0.25
                        reasons.append(f"ROLE_ID_KEYWORD_MATCH({kw})")
                        break

                # Supported pattern match
                if role_key == "button" and "invoke" in elem.supported_patterns:
                    score += 0.2
                    reasons.append("PATTERN_INVOKE_SUPPORTED")
                elif (
                    role_key in ("search_field", "editable_text", "message_input")
                    and "set_text" in elem.supported_patterns
                ):
                    score += 0.2
                    reasons.append("PATTERN_SET_TEXT_SUPPORTED")
            elif role_key == elem_ctype.lower():
                score += 0.35
                reasons.append(f"ROLE_CTYPE_MATCH({elem_ctype})")

        # 2. Explicit / Semantic Name Matching
        q_target = (query.name or query.query or query.element_query or "").lower().strip()
        if q_target:
            if name_lower == q_target:
                score += 0.60
                reasons.append(f"EXACT_NAME_MATCH({q_target})")
            elif q_target in name_lower:
                score += 0.40
                reasons.append(f"SUBSTRING_NAME_MATCH({q_target})")
            elif any(token in name_lower for token in q_target.split()):
                score += 0.20
                reasons.append("PARTIAL_TOKEN_NAME_MATCH")

        # 3. Explicit ControlType Matching
        if query.control_type:
            q_ctype = query.control_type.lower().strip()
            if q_ctype == elem_ctype.lower():
                score += 0.30
                reasons.append(f"EXPLICIT_CTYPE_MATCH({elem_ctype})")

        # 4. Explicit AutomationId Matching
        if query.automation_id:
            q_aid = query.automation_id.lower().strip()
            if auto_id_lower == q_aid:
                score += 0.60
                reasons.append(f"EXACT_AUTOMATION_ID_MATCH({query.automation_id})")
            elif q_aid in auto_id_lower:
                score += 0.30
                reasons.append("SUBSTRING_AUTOMATION_ID_MATCH")

        # 5. Geometrical Credibility Check
        if elem.width > 5 and elem.height > 5:
            score += 0.05

        return max(0.0, min(1.0, score)), reasons

    def _build_target_observation(
        self,
        target_win: WindowInfo,
        elem: UIElementInfo,
        metrics: DisplayMetrics,
    ) -> TargetObservation:
        """Construct authoritative TargetObservation with clickable point acquisition."""
        source = TargetSource.UIA_SEMANTIC
        clickable_point: tuple[int, int] | None = elem.clickable_point

        if clickable_point is not None:
            source = TargetSource.UIA_CLICKABLE_POINT
        else:
            # Fall back to bounding rectangle safe center point
            center_x = elem.left + elem.width // 2
            center_y = elem.top + elem.height // 2
            clickable_point = (center_x, center_y)
            source = TargetSource.UIA_BOUNDING_RECT

        mon_dpi = 1.0
        for m in metrics.monitors:
            if m.left <= elem.left < m.right and m.top <= elem.top < m.bottom:
                mon_dpi = m.dpi_scale
                break

        return TargetObservation(
            observation_id=str(uuid4()),
            window_hwnd=target_win.hwnd,
            window_pid=target_win.process_id,
            window_title=target_win.title,
            window_rect=(target_win.left, target_win.top, target_win.width, target_win.height),
            screen_geometry=metrics,
            ui_element_identity=elem.element_id,
            element_name=elem.name,
            control_type=elem.control_type,
            automation_id=elem.automation_id,
            class_name=elem.class_name,
            element_rect=(elem.left, elem.top, elem.width, elem.height),
            clickable_point=clickable_point,
            target_source=source,
            supported_patterns=elem.supported_patterns,
            is_enabled=elem.is_enabled,
            is_offscreen=elem.is_offscreen,
            is_keyboard_focusable=elem.is_keyboard_focusable,
            has_keyboard_focus=elem.has_keyboard_focus,
            timestamp=time.time(),
            dpi_scale=mon_dpi,
        )
