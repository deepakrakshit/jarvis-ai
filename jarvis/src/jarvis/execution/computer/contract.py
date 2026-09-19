"""Computer-Use Action and Observation Contracts.

Adapts OpenClaw CUA (computer-use-contract.ts) specifications into canonical Python dataclasses.
Provides standardized action requests, parameter envelopes, and verified observation models.
"""

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


class ComputerActionName(str, Enum):
    """Canonical computer-use action names adapted from OpenClaw v2 contract."""

    SCREENSHOT = "screenshot"
    LEFT_CLICK = "left_click"
    RIGHT_CLICK = "right_click"
    MIDDLE_CLICK = "middle_click"
    DOUBLE_CLICK = "double_click"
    TRIPLE_CLICK = "triple_click"
    MOUSE_MOVE = "mouse_move"
    LEFT_CLICK_DRAG = "left_click_drag"
    LEFT_MOUSE_DOWN = "left_mouse_down"
    LEFT_MOUSE_UP = "left_mouse_up"
    SCROLL = "scroll"
    TYPE = "type"
    KEY = "key"
    HOLD_KEY = "hold_key"
    WAIT = "wait"
    LIST_APPS = "list_apps"
    LIST_WINDOWS = "list_windows"
    GET_ACCESSIBILITY_TREE = "get_accessibility_tree"
    GET_CURSOR_POSITION = "get_cursor_position"
    GET_WINDOW_STATE = "get_window_state"
    LAUNCH_APP = "launch_app"
    KILL_APP = "kill_app"
    BRING_TO_FRONT = "bring_to_front"
    SET_VALUE = "set_value"
    ZOOM = "zoom"
    INVOKE_MENU = "invoke_menu"


class ScrollDirection(str, Enum):
    """Direction for scroll operations."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class DeliveryMode(str, Enum):
    """Execution delivery channel."""

    BACKGROUND = "background"
    FOREGROUND = "foreground"


class ActionResultEffect(str, Enum):
    """Verification outcome of an action execution."""

    CONFIRMED = "confirmed"
    UNVERIFIABLE = "unverifiable"
    SUSPECTED_NOOP = "suspected_noop"


@dataclass
class ComputerBounds:
    """Bounding box coordinates for visual or interactive elements."""

    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> Dict[str, int]:
        """Convert bounds to dictionary."""
        return asdict(self)


@dataclass
class ComputerActParams:
    """Canonical parameters for computer-use actions."""

    action: str
    execution_id: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    from_x: Optional[float] = None
    from_y: Optional[float] = None
    button: Optional[str] = None
    modifiers: Optional[str] = None
    text: Optional[str] = None
    keys: Optional[str] = None
    duration_ms: Optional[int] = None
    scroll_direction: Optional[str] = None
    scroll_amount: Optional[int] = None
    screen_index: Optional[int] = None
    ref_width: Optional[int] = None
    window_ref: Optional[str] = None
    element_ref: Optional[str] = None
    observation_id: Optional[str] = None
    delivery_mode: Optional[str] = None
    app: Optional[str] = None
    value: Optional[str] = None
    path: Optional[List[str]] = None
    query: Optional[str] = None
    depth: Optional[int] = None
    max_elements: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.execution_id:
            self.execution_id = str(uuid4())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation, omitting None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ComputerObservation:
    """Captured system state and observation context."""

    observation_id: str = field(default_factory=lambda: str(uuid4()))
    captured_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    active_window_title: Optional[str] = None
    active_window_hwnd: Optional[int] = None
    screenshot_path: Optional[str] = None
    elements_count: int = 0
    raw_tree: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert observation to dictionary."""
        return asdict(self)


@dataclass
class ComputerActResult:
    """Result envelope for executed computer actions."""

    ok: bool
    effect: str = ActionResultEffect.CONFIRMED.value
    observation: Optional[ComputerObservation] = None
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary representation."""
        res: Dict[str, Any] = {
            "ok": self.ok,
            "effect": self.effect,
            "details": self.details,
        }
        if self.observation:
            res["observation"] = self.observation.to_dict()
        if self.error:
            res["error"] = self.error
        return res
