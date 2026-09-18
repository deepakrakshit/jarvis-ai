"""Unit tests for Dynamic UI Automation Targeting, Clickable Points, and Safety Boundaries."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from jarvis.computer.emergency_stop import EmergencyStop
from jarvis.computer.executor import WindowsComputerExecutor
from jarvis.computer.models import (
    ComputerActionStatus,
    DiscoveredTarget,
    DisplayMetrics,
    TargetObservation,
    TargetQuery,
    TargetSource,
    UIElementInfo,
    WindowInfo,
)
from jarvis.computer.ui_targeting import UITargetResolver
from jarvis.tools.native import dispatch_native_tool


@pytest.fixture
def clean_emergency_stop() -> EmergencyStop:
    stop = EmergencyStop()
    stop.reset()
    return stop


def _make_mock_element(
    name: str = "Search or start a new chat",
    control_type: str = "Edit",
    automation_id: str = "search_box_id",
    left: int = 100,
    top: int = 200,
    width: int = 400,
    height: int = 40,
    is_enabled: bool = True,
    is_visible: bool = True,
    clickable_point: tuple[int, int] | None = (300, 220),
) -> UIElementInfo:
    return UIElementInfo(
        element_id="el_123",
        name=name,
        control_type=control_type,
        automation_id=automation_id,
        left=left,
        top=top,
        width=width,
        height=height,
        is_enabled=is_enabled,
        is_visible=is_visible,
        clickable_point=clickable_point,
        patterns=["ValuePattern"],
        children_count=0,
    )


def test_target_observation_expiry() -> None:
    """Verify that TargetObservation enforces bounded validity lifetime."""
    obs = TargetObservation(
        window_hwnd=123,
        window_pid=456,
        window_title="Active Application",
        window_rect=(0, 0, 1920, 1080),
        element_name="Search box",
        control_type="Edit",
        automation_id="search_id",
        element_rect=(100, 200, 400, 40),
        clickable_point=(300, 220),
        timestamp=time.time(),
        max_age_seconds=1.0,
    )
    assert obs.is_valid() is True

    # Expired observation
    old_obs = TargetObservation(
        window_hwnd=123,
        window_pid=456,
        window_title="Active Application",
        window_rect=(0, 0, 1920, 1080),
        element_name="Search box",
        control_type="Edit",
        automation_id="search_id",
        element_rect=(100, 200, 400, 40),
        clickable_point=(300, 220),
        timestamp=time.time() - 10.0,
        max_age_seconds=1.0,
    )
    assert old_obs.is_valid() is False


def test_emergency_stop_lifecycle(clean_emergency_stop: EmergencyStop) -> None:
    """Verify thread-safe emergency stop activation, latching, and reset."""
    assert clean_emergency_stop.is_activated is False

    clean_emergency_stop.activate(reason="Test safety interrupt")
    assert clean_emergency_stop.is_activated is True
    assert "Test safety interrupt" in clean_emergency_stop.reason

    with pytest.raises(RuntimeError, match="EMERGENCY_STOP_ACTIVATED"):
        clean_emergency_stop.verify_clear()

    clean_emergency_stop.reset()
    assert clean_emergency_stop.is_activated is False
    clean_emergency_stop.verify_clear()  # Should not raise


def test_ui_target_resolver_scoring_and_clickable_point() -> None:
    """Verify semantic role scoring and dynamic clickable point acquisition."""
    mock_uia = MagicMock()
    mock_win = MagicMock()
    mock_input = MagicMock()
    mock_stop = EmergencyStop()
    mock_stop.reset()

    target_win = WindowInfo(
        hwnd=12345,
        title="WhatsApp",
        class_name="Chrome_WidgetWin_1",
        process_id=9999,
        process_name="chrome.exe",
        left=0,
        top=0,
        width=1920,
        height=1080,
        is_visible=True,
        is_foreground=True,
        is_minimized=False,
        is_maximized=True,
    )
    mock_win.find_window.return_value = target_win
    mock_win.get_foreground_window.return_value = target_win
    mock_win.get_display_metrics.return_value = DisplayMetrics(
        primary_width=1920,
        primary_height=1080,
        virtual_left=0,
        virtual_top=0,
        virtual_width=1920,
        virtual_height=1080,
        monitors=[],
    )

    target_el = _make_mock_element(
        name="Search or start a new chat",
        control_type="Edit",
        left=200,
        top=300,
        width=400,
        height=50,
        clickable_point=(400, 325),
    )
    unrelated_el = _make_mock_element(
        name="Close",
        control_type="Button",
        automation_id="close_btn",
        left=1800,
        top=10,
        width=50,
        height=30,
        clickable_point=(1825, 25),
    )

    mock_uia.inspect_window_elements.return_value = [unrelated_el, target_el]
    mock_win.element_from_point.return_value = target_el

    resolver = UITargetResolver(
        windows_api=mock_win,
        uia=mock_uia,
        input_driver=mock_input,
        emergency_stop=mock_stop,
    )

    discovered = resolver.resolve_target(
        query=TargetQuery(query="search", semantic_role="Edit"),
        target_window=target_win,
    )

    assert discovered is not None
    assert discovered.element_info is not None
    assert discovered.element_info.name == "Search or start a new chat"
    assert discovered.observation.clickable_point == (400, 325)
    assert discovered.observation.target_source == TargetSource.UIA_CLICKABLE_POINT
    assert discovered.observation.is_valid() is True


def test_ui_target_resolver_occlusion_detection() -> None:
    """Verify live ElementFromPoint detects when target is occluded by another PID."""
    mock_uia = MagicMock()
    mock_win = MagicMock()
    mock_input = MagicMock()
    mock_stop = EmergencyStop()
    mock_stop.reset()

    target_obs = TargetObservation(
        window_hwnd=12345,
        window_pid=9999,
        window_title="WhatsApp",
        window_rect=(0, 0, 1920, 1080),
        element_name="Search or start a new chat",
        control_type="Edit",
        element_rect=(200, 300, 400, 50),
        clickable_point=(400, 325),
    )

    # Occluding element from another PID (e.g. 1111)
    occluding_el = _make_mock_element(
        name="Overlay Window",
        control_type="Window",
        left=100,
        top=100,
        width=800,
        height=600,
    )
    occluding_el.process_id = 1111

    mock_win.get_display_metrics.return_value = DisplayMetrics(
        primary_width=1920,
        primary_height=1080,
        virtual_left=0,
        virtual_top=0,
        virtual_width=1920,
        virtual_height=1080,
        monitors=[],
    )
    mock_win.get_foreground_window.return_value = 12345
    mock_win._user32.IsWindow.return_value = 1
    mock_win._user32.IsWindowVisible.return_value = 1
    mock_win.get_window_pid.return_value = 9999
    # Mock uia element_from_point returning occluding element
    mock_uia.element_from_point.return_value = occluding_el

    resolver = UITargetResolver(
        windows_api=mock_win,
        uia=mock_uia,
        input_driver=mock_input,
        emergency_stop=mock_stop,
    )

    is_valid, reason = resolver.validate_element_at_point(400, 325, target_obs)
    assert is_valid is False
    assert "STALE_TARGET" in reason or "PID" in reason


@pytest.mark.asyncio
async def test_executor_resolve_target_and_click_element_capabilities() -> None:
    """Verify execution of computer:resolve_target and computer:click_element capabilities."""
    executor = WindowsComputerExecutor()

    mock_target_el = _make_mock_element(name="Test Button", control_type="Button")
    mock_discovered = DiscoveredTarget(
        observation=TargetObservation(
            window_hwnd=123,
            window_pid=456,
            window_title="Test App",
            window_rect=(0, 0, 1920, 1080),
            element_name="Test Button",
            control_type="Button",
            automation_id="test_btn",
            element_rect=(450, 380, 100, 40),
            clickable_point=(500, 400),
            timestamp=time.time(),
        ),
        score=150.0,
        element_info=mock_target_el,
    )
    executor.target_resolver.resolve_target = MagicMock(return_value=mock_discovered)  # type: ignore[method-assign]
    executor.target_resolver.validate_element_at_point = MagicMock(return_value=(True, "OK"))  # type: ignore[method-assign]
    executor.target_resolver.is_observation_valid = MagicMock(return_value=(True, "OK"))  # type: ignore[method-assign]

    # Test resolve_target capability
    res_resolve = await executor.execute_capability(
        "computer:resolve_target",
        {"query": "Test Button", "semantic_role": "Button"},
    )
    assert res_resolve.status == ComputerActionStatus.EXECUTED
    assert res_resolve.target == "Test Button"
    assert res_resolve.details["clickable_point"] == (500, 400)

    # Test click_element capability
    mock_click = MagicMock(return_value={"status": "success", "pixel_x": 500, "pixel_y": 400})
    executor.input_driver.click = mock_click  # type: ignore[method-assign]
    res_click = await executor.execute_capability(
        "computer:click_element",
        {"element_query": "Test Button"},
    )
    assert res_click.status == ComputerActionStatus.EXECUTED
    assert res_click.target == "Test Button"


@pytest.mark.asyncio
async def test_executor_inspect_ui_alias_resolution() -> None:
    """Verify computer:ui:inspect is accepted and handled properly."""
    executor = WindowsComputerExecutor()
    mock_el = _make_mock_element(name="Window Header", control_type="Text")
    executor.uia.inspect_window_elements = MagicMock(return_value=[mock_el])  # type: ignore[method-assign]
    executor.windows_api.find_window = MagicMock(  # type: ignore[method-assign]
        return_value=WindowInfo(
            hwnd=555,
            title="Active App",
            class_name="AppClass",
            process_id=1234,
            process_name="app.exe",
            left=0,
            top=0,
            width=800,
            height=600,
            is_visible=True,
            is_foreground=True,
            is_minimized=False,
            is_maximized=False,
        )
    )

    res = await executor.execute_capability("computer:ui:inspect", {"window_title": "Active App"})
    assert res.status == ComputerActionStatus.EXECUTED
    assert res.action == "computer:inspect_ui"
    assert "elements" in res.details
    assert len(res.details["elements"]) == 1


@pytest.mark.asyncio
async def test_native_dispatcher_resolve_target_and_click() -> None:
    """Verify dispatch_native_tool routes computer:resolve_target and jarvis_click."""
    from jarvis.computer import get_computer_executor

    executor = get_computer_executor()
    mock_el = _make_mock_element(name="New tab", control_type="Button")
    mock_disc = DiscoveredTarget(
        observation=TargetObservation(
            window_hwnd=999,
            window_pid=888,
            window_title="Browser",
            window_rect=(0, 0, 1920, 1080),
            element_name="New tab",
            control_type="Button",
            automation_id="tab_add",
            element_rect=(230, 30, 40, 30),
            clickable_point=(250, 45),
            timestamp=time.time(),
        ),
        score=100.0,
        element_info=mock_el,
    )
    mock_resolve = MagicMock(return_value=mock_disc)
    mock_val_pt = MagicMock(return_value=(True, "OK"))
    mock_val_obs = MagicMock(return_value=(True, "OK"))
    mock_driver_click = MagicMock(return_value={"status": "success", "pixel_x": 250, "pixel_y": 45})
    orig_resolve = executor.target_resolver.resolve_target
    orig_val_pt = executor.target_resolver.validate_element_at_point
    orig_val_obs = executor.target_resolver.is_observation_valid
    orig_click = executor.input_driver.click

    try:
        executor.target_resolver.resolve_target = mock_resolve  # type: ignore[method-assign]
        executor.target_resolver.validate_element_at_point = mock_val_pt  # type: ignore[method-assign]
        executor.target_resolver.is_observation_valid = mock_val_obs  # type: ignore[method-assign]
        executor.input_driver.click = mock_driver_click  # type: ignore[method-assign]

        # Dispatch jarvis_resolve_target
        resolve_out = await dispatch_native_tool("jarvis_resolve_target", {"query": "New tab"})
        assert resolve_out["status"] == "EXECUTED"
        assert resolve_out["target"] == "New tab"

        # Dispatch jarvis_click with element_query
        click_out = await dispatch_native_tool("jarvis_click", {"element_query": "New tab"})
        assert click_out["status"] == "success"
        assert click_out["target"] == "New tab"
    finally:
        executor.target_resolver.resolve_target = orig_resolve  # type: ignore[method-assign]
        executor.target_resolver.validate_element_at_point = orig_val_pt  # type: ignore[method-assign]
        executor.target_resolver.is_observation_valid = orig_val_obs  # type: ignore[method-assign]
        executor.input_driver.click = orig_click  # type: ignore[method-assign]
