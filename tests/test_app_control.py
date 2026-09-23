"""Automated Tests for Deep In-App Control and UI Automation Subsystem.

Verifies:
- Dynamic Application Registry discovery and resolution
- Window Manager enumeration and scoring disambiguation
- Computer-Use contract serialization and execution
- Semantic target resolution with confidence scoring
- Universal Application Control Engine closed-loop execution
- Action Broker capability dispatch for application control
"""

from typing import List

import pytest

from jarvis.actions.broker import ActionBroker
from jarvis.contracts.action import ActionRequest, ActionStatus, ExecutionTarget, RiskTier
from jarvis.core.app_control_engine import (
    AppControlEngine,
    ProviderType,
)
from jarvis.execution.computer.contract import (
    ActionResultEffect,
    ComputerActionName,
    ComputerActParams,
    ComputerActResult,
    ComputerBounds,
    ComputerObservation,
)
from jarvis.execution.windows.app_registry import app_registry
from jarvis.execution.windows.host import windows_node
from jarvis.execution.windows.uia import UIElementInfo
from jarvis.execution.windows.window_manager import window_manager
from jarvis.policy.engine import PolicyEngine
from jarvis.policy.firewall import (
    CAPABILITY_APP_LAUNCH,
    CAPABILITY_COMPUTER_ACT,
    CAPABILITY_RISK_MAP,
    CAPABILITY_UI_INSPECT,
    CAPABILITY_UI_INTERACT,
    CAPABILITY_WINDOW_CLOSE,
    CAPABILITY_WINDOW_FOCUS,
    CAPABILITY_WINDOW_LIST,
)


@pytest.fixture(autouse=True)
def setup_node_capabilities() -> None:
    """Ensure host capabilities are registered before each test."""
    windows_node.register_capabilities()


def test_app_registry_discovery() -> None:
    """Verify dynamic discovery of installed applications without hardcoding."""
    apps = app_registry.discover_installed_apps(force_refresh=True)
    assert isinstance(apps, list)
    assert len(apps) > 0

    # Test resolution
    app_names = [a.name.lower() for a in apps]
    assert any(
        "explorer" in n or "system" in n or "terminal" in n or "code" in n for n in app_names
    )

    found = app_registry.find_application("explorer")
    assert found is not None
    assert "explorer" in found.name.lower() or (
        found.executable_path and "explorer" in found.executable_path.lower()
    )


def test_window_manager_enumeration() -> None:
    """Verify live window enumeration returns structured metadata."""
    windows = window_manager.list_windows(visible_only=True)
    assert isinstance(windows, list)
    assert len(windows) > 0

    for win in windows:
        assert isinstance(win.hwnd, int)
        assert win.hwnd > 0
        assert isinstance(win.title, str)
        assert isinstance(win.bounds, dict)
        assert "left" in win.bounds
        assert "width" in win.bounds


def test_window_manager_find_and_disambiguation() -> None:
    """Verify window finding applies smart scoring and disambiguation."""
    windows = window_manager.list_windows(visible_only=True)
    if not windows:
        pytest.skip("No open windows to test find_window")

    first_win = windows[0]
    # Exact title search
    matched = window_manager.find_window(first_win.title)
    assert matched is not None
    assert matched.hwnd == first_win.hwnd

    # Non-matching search returns None
    none_match = window_manager.find_window("__non_existent_window_title_query_xyz_99__")
    assert none_match is None


def test_computer_use_contract() -> None:
    """Verify Computer-Use contract models and dictionary projections."""
    bounds = ComputerBounds(x=10, y=20, width=300, height=200)
    assert bounds.to_dict() == {"x": 10, "y": 20, "width": 300, "height": 200}

    params = ComputerActParams(
        action=ComputerActionName.LEFT_CLICK.value,
        x=150.0,
        y=250.0,
        button="left",
    )
    p_dict = params.to_dict()
    assert p_dict["action"] == "left_click"
    assert p_dict["x"] == 150.0
    assert p_dict["y"] == 250.0
    assert "execution_id" in p_dict

    obs = ComputerObservation(active_window_title="Test Window", active_window_hwnd=12345)
    result = ComputerActResult(
        ok=True,
        effect=ActionResultEffect.CONFIRMED.value,
        observation=obs,
        details={"step": "click_performed"},
    )
    res_dict = result.to_dict()
    assert res_dict["ok"] is True
    assert res_dict["effect"] == "confirmed"
    assert res_dict["observation"]["active_window_hwnd"] == 12345


def test_semantic_target_resolution() -> None:
    """Verify semantic target resolution with confidence scoring."""
    engine = AppControlEngine()

    mock_controls: List[UIElementInfo] = [
        UIElementInfo(
            automation_id="btn_save",
            name="Save",
            control_type="Button",
            control_type_id=50000,
            class_name="ButtonClass",
            is_enabled=True,
            is_offscreen=False,
            left=100,
            top=100,
            width=50,
            height=30,
            supported_patterns=["Invoke"],
            value=None,
        ),
        UIElementInfo(
            automation_id="txt_input",
            name="Filename",
            control_type="Edit",
            control_type_id=50004,
            class_name="EditClass",
            is_enabled=True,
            is_offscreen=False,
            left=100,
            top=150,
            width=200,
            height=30,
            supported_patterns=["Value"],
            value="document.txt",
        ),
        UIElementInfo(
            automation_id="chk_backup",
            name="Create Backup",
            control_type="CheckBox",
            control_type_id=50002,
            class_name="CheckClass",
            is_enabled=True,
            is_offscreen=False,
            left=100,
            top=200,
            width=150,
            height=20,
            supported_patterns=["Toggle"],
            value=None,
        ),
    ]

    # 1. Exact AutomationId match (1.0 confidence)
    res_id = engine.resolve_target(hwnd=1, query="btn_save", controls=mock_controls)
    assert res_id.element is not None
    assert res_id.element.automation_id == "btn_save"
    assert res_id.confidence == 1.0
    assert res_id.provider == ProviderType.WINDOWS_UIA

    # 2. Exact Name match (0.95 confidence)
    res_name = engine.resolve_target(hwnd=1, query="Save", controls=mock_controls)
    assert res_name.element is not None
    assert res_name.element.name == "Save"
    assert res_name.confidence == 0.95

    # 3. Substring Name match (0.85 confidence)
    res_sub = engine.resolve_target(hwnd=1, query="Backup", controls=mock_controls)
    assert res_sub.element is not None
    assert res_sub.element.name == "Create Backup"
    assert res_sub.confidence >= 0.85

    # 4. Value match (0.65 confidence)
    res_val = engine.resolve_target(hwnd=1, query="document", controls=mock_controls)
    assert res_val.element is not None
    assert res_val.element.automation_id == "txt_input"
    assert res_val.confidence >= 0.65

    # 5. Non-matching query falls back to Computer-Use
    res_none = engine.resolve_target(hwnd=1, query="NonExistentControl", controls=mock_controls)
    assert res_none.element is None
    assert res_none.confidence == 0.0
    assert res_none.provider == ProviderType.COMPUTER_FALLBACK


def test_app_control_engine_computer_actions() -> None:
    """Verify execution of computer action contract dispatch."""
    engine = AppControlEngine()

    # Test wait action
    wait_params = ComputerActParams(action=ComputerActionName.WAIT.value, duration_ms=50)
    res_wait = engine.execute_computer_action(wait_params)
    assert res_wait.ok is True
    assert res_wait.effect == ActionResultEffect.CONFIRMED.value

    # Test list_windows action
    win_params = ComputerActParams(action=ComputerActionName.LIST_WINDOWS.value)
    res_win = engine.execute_computer_action(win_params)
    assert res_win.ok is True
    assert "windows" in res_win.details


@pytest.mark.asyncio
async def test_action_broker_app_control_integration() -> None:
    """Verify ActionBroker routes application control capabilities through policy engine."""
    broker = ActionBroker(policy=PolicyEngine())

    # 1. Window list capability
    win_req = ActionRequest(
        task_id="TASK-APP-1",
        session_id="SESS-01",
        capability=CAPABILITY_WINDOW_LIST,
        arguments={},
        target=ExecutionTarget.WINDOWS_NODE,
    )
    res = await broker.execute(win_req)
    assert res.status == ActionStatus.SUCCEEDED
    assert isinstance(res.output, list)

    # 2. Computer action capability (wait)
    wait_req = ActionRequest(
        task_id="TASK-APP-2",
        session_id="SESS-01",
        capability=CAPABILITY_COMPUTER_ACT,
        arguments={"action": "wait", "duration_ms": 20},
        target=ExecutionTarget.WINDOWS_NODE,
    )
    res_act = await broker.execute(wait_req)
    assert res_act.status == ActionStatus.SUCCEEDED
    assert res_act.output.get("ok") is True


def test_policy_firewall_risk_tiers() -> None:
    """Verify all new application control capabilities have risk tier assignments."""
    assert CAPABILITY_APP_LAUNCH in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_APP_LAUNCH] == RiskTier.MEDIUM

    assert CAPABILITY_WINDOW_FOCUS in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_WINDOW_FOCUS] == RiskTier.LOW

    assert CAPABILITY_WINDOW_CLOSE in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_WINDOW_CLOSE] == RiskTier.MEDIUM

    assert CAPABILITY_WINDOW_LIST in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_WINDOW_LIST] == RiskTier.READ_ONLY

    assert CAPABILITY_UI_INSPECT in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_UI_INSPECT] == RiskTier.READ_ONLY

    assert CAPABILITY_UI_INTERACT in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_UI_INTERACT] == RiskTier.MEDIUM

    assert CAPABILITY_COMPUTER_ACT in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_COMPUTER_ACT] == RiskTier.MEDIUM
