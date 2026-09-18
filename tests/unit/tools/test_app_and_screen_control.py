"""Unit test suite for OS application lifecycle and desktop screen control capabilities."""

import platform

import pytest
from PIL import Image

from jarvis.agents.base import SpecialistRole
from jarvis.agents.computer import ComputerSpecialist
from jarvis.agents.router import RoutingCategory, SpecialistRouter
from jarvis.core.capabilities.builtin import BUILTIN_CAPABILITIES
from jarvis.tools.native import (
    close_application,
    dispatch_native_tool,
    launch_application,
    list_running_applications,
)
from jarvis.tools.native.screen_control import (
    capture_screen,
    click_coordinate,
    get_active_window,
    press_hotkey,
    type_text,
)


@pytest.mark.unit
class TestAppLifecycleControl:
    """Tests for native desktop application launching, closing, and enumeration."""

    def test_app_control_manifests_registered(self) -> None:
        """Verify app control tools are registered in builtin capability registry."""
        cap_ids = {c.capability_id for c in BUILTIN_CAPABILITIES}
        assert "native:app:launch" in cap_ids
        assert "native:app:close" in cap_ids
        assert "native:app:list" in cap_ids

    def test_launch_application_empty_name_raises(self) -> None:
        """Verify empty application name fails closed."""
        with pytest.raises(ValueError, match="cannot be empty"):
            launch_application("")

    def test_launch_application_injection_delimiter_raises(self) -> None:
        """Verify application names with shell injection characters fail closed."""
        with pytest.raises(ValueError, match="invalid characters"):
            launch_application("notepad.exe & calc.exe")

    def test_close_application_protected_system_process_fails_closed(self) -> None:
        """Verify protected OS processes cannot be closed by the agent."""
        with pytest.raises(PermissionError, match="Refusing to terminate protected system process"):
            close_application("csrss.exe")

        with pytest.raises(PermissionError, match="Refusing to terminate protected system process"):
            close_application("0")  # PID 0 (System Idle)

    def test_close_application_nonexistent_process_returns_not_found(self) -> None:
        """Verify trying to close a non-existent app returns a clean not_found status."""
        res = close_application("non_existent_fake_app_xyz_999.exe")
        assert res["status"] == "not_found"
        assert res["count"] == 0 if "count" in res else len(res.get("terminated_pids", [])) == 0

    def test_list_running_applications(self) -> None:
        """Verify active process enumeration returns valid metadata."""
        res = list_running_applications(limit=10)
        assert res["status"] == "success"
        assert res["count"] > 0
        assert len(res["processes"]) <= 10
        first = res["processes"][0]
        assert "pid" in first
        assert "name" in first
        assert "memory_percent" in first

    @pytest.mark.skipif(
        platform.system() != "Windows", reason="Windows desktop required for notepad"
    )
    def test_launch_and_close_real_app(self) -> None:
        """Launch notepad, verify PID is tracked, and gracefully close it."""
        launch_res = launch_application("notepad")
        assert launch_res["status"] == "success"
        pid = launch_res["pid"]
        assert pid > 0

        # Close by name or PID
        close_res = close_application(str(pid))
        assert close_res["status"] == "success"
        assert pid in close_res["terminated_pids"]


@pytest.mark.unit
class TestScreenPerceptionAndInputControl:
    """Tests for screen frame capture, mouse click validation, typing, and window inspection."""

    def test_screen_control_manifests_registered(self) -> None:
        """Verify screen control tools are registered in builtin capability registry."""
        cap_ids = {c.capability_id for c in BUILTIN_CAPABILITIES}
        assert "native:screen:capture" in cap_ids
        assert "native:screen:click" in cap_ids
        assert "native:screen:type" in cap_ids
        assert "native:screen:hotkey" in cap_ids
        assert "native:screen:get_window" in cap_ids

    def test_capture_screen_with_test_double(self) -> None:
        """Verify screen capture processes and encodes a test image properly."""
        test_img = Image.new("RGB", (1920, 1080), color=(73, 109, 137))
        res = capture_screen(output_format="base64", max_dimension=1280, test_double_image=test_img)
        assert res["status"] == "success"
        assert res["width"] == 1280
        assert res["height"] == 720
        assert "base64_data" in res
        assert res["mime_type"] == "image/jpeg"
        assert res["byte_size"] > 0

    def test_click_coordinate_out_of_bounds_raises(self) -> None:
        """Verify coordinate outside display bounds raises ValueError."""
        with pytest.raises(ValueError, match="exceeds display bounds"):
            click_coordinate(x=-100, y=500)

    def test_type_text_empty_raises(self) -> None:
        """Verify empty text input raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            type_text("")

    def test_press_hotkey_empty_raises(self) -> None:
        """Verify empty hotkey list raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            press_hotkey([])

    def test_get_active_window_structure(self) -> None:
        """Verify active window inspection returns structured response."""
        res = get_active_window()
        assert res["status"] in ("success", "not_found", "unavailable")
        assert res["action"] == "get_window"


@pytest.mark.unit
@pytest.mark.asyncio
class TestDispatcherAndSpecialistIntegration:
    """Tests for native capability dispatch and ComputerSpecialist proposals."""

    async def test_dispatch_app_tools(self) -> None:
        """Verify dispatch_native_tool routes app control IDs correctly."""
        list_res = await dispatch_native_tool("native:app:list", {"limit": 5})
        assert list_res["status"] == "success"
        assert len(list_res["processes"]) <= 5

    async def test_dispatch_screen_tools(self) -> None:
        """Verify dispatch_native_tool routes screen control IDs."""
        win_res = await dispatch_native_tool("native:screen:get_window", {})
        assert win_res["action"] == "get_window"

    async def test_computer_specialist_propose_and_synthesize(self) -> None:
        """Verify ComputerSpecialist proposes and synthesizes app and screen actions."""
        spec = ComputerSpecialist(model_gateway=None)

        # Propose launch app
        prop_launch = await spec.propose("open notepad")
        assert prop_launch.tool_id == "native:app:launch"
        assert prop_launch.arguments.get("app_name") == "notepad"

        # Propose close app
        prop_close = await spec.propose("close notepad")
        assert prop_close.tool_id == "native:app:close"
        assert prop_close.arguments.get("app_name") == "notepad"

        # Propose list apps
        prop_list = await spec.propose("running apps")
        assert prop_list.tool_id == "native:app:list"

        # Propose screenshot
        prop_shot = await spec.propose("take screenshot")
        assert prop_shot.tool_id == "native:screen:capture"

        # Propose active window
        prop_win = await spec.propose("active window")
        assert prop_win.tool_id == "native:screen:get_window"

        # Synthesize app launch
        synth_launch = await spec.synthesize(
            prop_launch,
            {"status": "success", "message": "Successfully launched 'notepad' (PID: 1234)."},
            "open notepad",
        )
        assert "Application Launched" in synth_launch
        assert "1234" in synth_launch

        # Synthesize app close
        synth_close = await spec.synthesize(
            prop_close,
            {
                "status": "success",
                "message": "Successfully closed 1 process(es) matching 'notepad'.",
            },
            "close notepad",
        )
        assert "Application Closed" in synth_close

    async def test_specialist_router_heuristics(self) -> None:
        """Verify router classifies app and screen commands to the computer specialist."""
        router = SpecialistRouter(model_gateway=None)

        dec1 = router._heuristic_route("open notepad")
        assert dec1.category == RoutingCategory.SPECIALIST
        assert dec1.specialist_role == SpecialistRole.COMPUTER

        dec2 = router._heuristic_route("close notepad")
        assert dec2.category == RoutingCategory.SPECIALIST
        assert dec2.specialist_role == SpecialistRole.COMPUTER

        dec3 = router._heuristic_route("running apps")
        assert dec3.category == RoutingCategory.SPECIALIST
        assert dec3.specialist_role == SpecialistRole.COMPUTER

        dec4 = router._heuristic_route("take screenshot")
        assert dec4.category == RoutingCategory.SPECIALIST
        assert dec4.specialist_role == SpecialistRole.COMPUTER
