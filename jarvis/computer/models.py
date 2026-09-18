"""Domain entities and data contracts for the JARVIS Computer Control Subsystem."""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ComputerActionReasonCode(StrEnum):
    """Categorical structured failure reason codes for computer control."""

    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    WINDOW_NOT_VISIBLE = "WINDOW_NOT_VISIBLE"
    WINDOW_NOT_FOREGROUND = "WINDOW_NOT_FOREGROUND"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    UI_ELEMENT_NOT_FOUND = "UI_ELEMENT_NOT_FOUND"
    PRECONDITION_NOT_SATISFIED = "PRECONDITION_NOT_SATISFIED"
    STALE_SCREEN_STATE = "STALE_SCREEN_STATE"
    STALE_TARGET = "STALE_TARGET"
    CHAT_RECIPIENT_MISMATCH = "CHAT_RECIPIENT_MISMATCH"
    INPUT_BLOCKED_BY_INTEGRITY_BOUNDARY = "INPUT_BLOCKED_BY_INTEGRITY_BOUNDARY"
    CONTROL_PATTERN_UNAVAILABLE = "CONTROL_PATTERN_UNAVAILABLE"
    ELEMENT_OBSCURED = "ELEMENT_OBSCURED"
    TARGET_OFFSCREEN = "TARGET_OFFSCREEN"
    EMERGENCY_STOP_ACTIVATED = "EMERGENCY_STOP_ACTIVATED"
    POLICY_DENIED = "POLICY_DENIED"
    HITL_REQUIRED = "HITL_REQUIRED"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    TARGET_EXITED = "TARGET_EXITED"
    DESKTOP_UNAVAILABLE = "DESKTOP_UNAVAILABLE"
    SECURE_DESKTOP = "SECURE_DESKTOP"
    ACTION_TIMEOUT = "ACTION_TIMEOUT"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class TargetSource(StrEnum):
    """Categorical source provenance for computer action targets."""

    UIA_CLICKABLE_POINT = "UIA_CLICKABLE_POINT"
    UIA_BOUNDING_RECT = "UIA_BOUNDING_RECT"
    UIA_SEMANTIC = "UIA_SEMANTIC"
    VISUAL_GROUNDED = "VISUAL_GROUNDED"
    EXPLICIT_OPERATOR = "EXPLICIT_OPERATOR"
    UNVALIDATED_GUESS = "UNVALIDATED_GUESS"


class ComputerActionState(StrEnum):
    """Execution state machine governing dependent computer actions."""

    DESKTOP_UNKNOWN = "DESKTOP_UNKNOWN"
    DESKTOP_OBSERVED = "DESKTOP_OBSERVED"
    TARGET_RESOLVED = "TARGET_RESOLVED"
    TARGET_FOCUSED = "TARGET_FOCUSED"
    ACTION_EXECUTING = "ACTION_EXECUTING"
    POST_ACTION_OBSERVING = "POST_ACTION_OBSERVING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class DesktopState(StrEnum):
    """Windows interactive desktop state classifications."""

    NORMAL_DESKTOP = "NORMAL_DESKTOP"
    ELEVATED_DESKTOP = "ELEVATED_DESKTOP"
    SECURE_DESKTOP = "SECURE_DESKTOP"
    LOCKED_DESKTOP = "LOCKED_DESKTOP"
    UNAVAILABLE = "UNAVAILABLE"


class ComputerActionStatus(StrEnum):
    """Status lifecycle for computer execution and verification."""

    REQUESTED = "REQUESTED"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    DENIED = "DENIED"
    UNVERIFIED = "UNVERIFIED"


class ResolutionStatus(StrEnum):
    """Application resolution pipeline classification."""

    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    LAUNCH_FAILED = "LAUNCH_FAILED"
    LAUNCHED_UNVERIFIED = "LAUNCHED_UNVERIFIED"
    LAUNCHED_AND_VERIFIED = "LAUNCHED_AND_VERIFIED"


class MouseButton(StrEnum):
    """Supported mouse input buttons."""

    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class WindowAction(StrEnum):
    """Window management actions."""

    FOCUS = "focus"
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    RESTORE = "restore"
    CLOSE = "close"


class UIAControlType(StrEnum):
    """Standard Windows UI Automation control types."""

    WINDOW = "Window"
    BUTTON = "Button"
    EDIT = "Edit"
    TEXT = "Text"
    MENU = "Menu"
    MENU_ITEM = "MenuItem"
    TAB = "Tab"
    TAB_ITEM = "TabItem"
    LIST = "List"
    LIST_ITEM = "ListItem"
    CHECKBOX = "CheckBox"
    RADIO_BUTTON = "RadioButton"
    COMBO_BOX = "ComboBox"
    HYPERLINK = "Hyperlink"
    DOCUMENT = "Document"
    PANE = "Pane"
    CUSTOM = "Custom"


class MonitorInfo(BaseModel):
    """Display monitor boundary and DPI configuration."""

    monitor_index: int
    is_primary: bool
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int
    dpi_scale: float = 1.0


class DisplayMetrics(BaseModel):
    """Virtual desktop metrics spanning all active monitors."""

    virtual_left: int
    virtual_top: int
    virtual_width: int
    virtual_height: int
    primary_width: int
    primary_height: int
    monitors: list[MonitorInfo] = Field(default_factory=list)


class WindowInfo(BaseModel):
    """Live Windows desktop window metadata."""

    hwnd: int
    title: str
    class_name: str
    process_id: int
    process_name: str
    left: int
    top: int
    width: int
    height: int
    is_visible: bool
    is_foreground: bool
    is_minimized: bool
    is_maximized: bool


class ProcessInfo(BaseModel):
    """Inspected host process metadata."""

    pid: int
    name: str
    exe_path: str | None = None
    status: str = "running"
    create_time: float = 0.0
    memory_percent: float = 0.0
    has_visible_window: bool = False
    window_titles: list[str] = Field(default_factory=list)


class AppResolution(BaseModel):
    """Result of application name resolution pipeline."""

    status: ResolutionStatus
    app_name: str
    resolved_path: str | None = None
    resolution_source: str | None = None
    command_args: list[str] = Field(default_factory=list)
    existing_hwnd: int | None = None
    diagnostic_message: str = ""


class UIElementInfo(BaseModel):
    """Discovered Windows UI Automation element."""

    element_id: str
    name: str
    control_type: str
    automation_id: str = ""
    class_name: str = ""
    left: int
    top: int
    width: int
    height: int
    is_enabled: bool = True
    is_visible: bool = True
    is_offscreen: bool = False
    is_keyboard_focusable: bool = False
    has_keyboard_focus: bool = False
    process_id: int = 0
    center_x: int = 0
    center_y: int = 0
    clickable_point: tuple[int, int] | None = None
    supported_patterns: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    children_count: int = 0


class TargetObservation(BaseModel):
    """Authoritative observed state of a discovered UI target bound to a specific observation cycle."""

    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    window_hwnd: int
    window_pid: int = 0
    window_title: str = ""
    window_rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, width, height
    screen_geometry: DisplayMetrics | None = None
    ui_element_identity: str = ""
    element_name: str = ""
    control_type: str = ""
    automation_id: str = ""
    class_name: str = ""
    runtime_id: list[int] | str | None = None
    element_rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, width, height
    clickable_point: tuple[int, int] | None = None
    target_source: TargetSource = TargetSource.UIA_SEMANTIC
    supported_patterns: list[str] = Field(default_factory=list)
    is_enabled: bool = True
    is_offscreen: bool = False
    is_keyboard_focusable: bool = False
    has_keyboard_focus: bool = False
    timestamp: float = Field(default_factory=time.time)
    dpi_scale: float = 1.0
    max_age_seconds: float = 10.0

    def is_valid(self, max_age: float | None = None) -> bool:
        """Assert whether this target observation is within valid freshness bounds."""
        effective_max = max_age if max_age is not None else self.max_age_seconds
        return (time.time() - self.timestamp) <= effective_max


class TargetQuery(BaseModel):
    """Structured semantic query for dynamic UI targeting without hardcoded coordinates."""

    query: str | None = None
    element_query: str | None = None
    semantic_role: str | None = (
        None  # e.g. search_field, button, editable_text, tab, checkbox, message_input
    )
    name: str | None = None
    description: str | None = None
    control_type: str | None = None
    automation_id: str | None = None
    class_name: str | None = None
    value: str | None = None
    window_title: str | None = None
    hwnd: int | None = None
    intent: str | None = None  # click, type, focus, toggle, select, scroll


class DiscoveredTarget(BaseModel):
    """Evaluated UI target match with confidence score and resolution metadata."""

    observation: TargetObservation
    score: float = 0.0
    match_reasons: list[str] = Field(default_factory=list)
    element_info: UIElementInfo | None = None
    is_ambiguous: bool = False
    candidate_count: int = 1


class Coordinate(BaseModel):
    """Desktop coordinate representation supporting normalized and physical pixels."""

    x: float
    y: float
    normalized: bool = False
    monitor_index: int = 1


class ScreenshotMetadata(BaseModel):
    """Metadata describing a captured desktop display frame."""

    capture_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: float = Field(default_factory=time.time)
    width: int
    height: int
    original_width: int
    original_height: int
    monitor_index: int = 1
    mime_type: str = "image/jpeg"
    byte_size: int = 0
    sha256_digest: str = ""

    @field_validator("capture_id", mode="before")
    @classmethod
    def _coerce_capture_id(cls, v: Any) -> str:
        return str(v) if v is not None else str(uuid4())


class ScreenshotResult(BaseModel):
    """Full screenshot capture outcome."""

    status: str = "success"
    metadata: ScreenshotMetadata
    raw_bytes: bytes = b""
    base64_data: str | None = None
    error_code: str | None = None
    message: str = ""


class ComputerActionResult(BaseModel):
    """Unified result structure returned by all computer capabilities."""

    action: str
    status: ComputerActionStatus
    target: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    execution_duration_ms: float = 0.0
    verification_verdict: str = "UNVERIFIED"
    verification_details: dict[str, Any] = Field(default_factory=dict)
    screenshot_metadata: ScreenshotMetadata | None = None
    error: str | None = None
    reason_code: ComputerActionReasonCode | str | None = None
    retryable: bool = False
    suggested_action: str | None = None
    message: str = ""
    diagnostic_context: dict[str, Any] = Field(default_factory=dict)
    target_source: TargetSource | None = None
    target_observation: TargetObservation | None = None

    def to_structured_dict(self) -> dict[str, Any]:
        """Produce a canonical, structured JSON-serializable outcome dictionary."""
        effective_status = self.status.value
        msg = self.message or self.error or ""
        if not msg:
            if effective_status in ("EXECUTED", "VERIFIED"):
                msg = f"Action '{self.action}' succeeded."
            else:
                msg = f"Action '{self.action}' failed: {self.reason_code or 'Unknown error'}."

        res: dict[str, Any] = {
            "status": effective_status,
            "action": self.action,
            "operation": self.action,
            "target": self.target or "desktop",
            "message": msg,
            "verification_verdict": self.verification_verdict,
            "execution_duration_ms": self.execution_duration_ms,
            "retryable": self.retryable,
        }

        if self.target_source:
            res["target_source"] = self.target_source.value

        if self.target_observation:
            res["target_observation"] = self.target_observation.model_dump()

        if self.reason_code:
            res["reason_code"] = (
                self.reason_code.value
                if isinstance(self.reason_code, ComputerActionReasonCode)
                else str(self.reason_code)
            )

        if self.suggested_action:
            res["suggested_action"] = self.suggested_action

        if self.error:
            res["error"] = self.error

        if self.details:
            res["details"] = self.details

        if self.diagnostic_context:
            res["diagnostic_context"] = self.diagnostic_context

        if self.verification_details:
            res["verification"] = self.verification_details

        return res
