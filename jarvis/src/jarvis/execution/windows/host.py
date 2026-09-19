"""Central Windows Node Execution Engine for JARVIS.

Registers native Windows capabilities into the Action Broker capability registry.
"""

from jarvis.actions.registry import capability_registry
from jarvis.execution.computer.contract import ComputerActParams
from jarvis.execution.windows.desktop import (
    capture_screenshot,
    mouse_click,
    press_key,
    type_text,
)
from jarvis.execution.windows.filesystem import (
    delete_file,
    list_directory,
    read_file,
    write_file,
)
from jarvis.execution.windows.process import (
    enumerate_processes,
    inspect_process,
    launch_process,
    terminate_process,
)
from jarvis.execution.windows.shell import run_shell_command
from jarvis.execution.windows.system import (
    get_display_brightness,
    get_system_info,
    get_system_volume,
    set_display_brightness,
    set_system_volume,
)
from jarvis.policy.firewall import (
    CAPABILITY_APP_LAUNCH,
    CAPABILITY_COMPUTER_ACT,
    CAPABILITY_COMPUTER_CLICK,
    CAPABILITY_COMPUTER_KEY,
    CAPABILITY_COMPUTER_SCREENSHOT,
    CAPABILITY_COMPUTER_TYPE,
    CAPABILITY_FILESYSTEM_DELETE,
    CAPABILITY_FILESYSTEM_LIST,
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_FILESYSTEM_WRITE,
    CAPABILITY_PROCESS_ENUMERATE,
    CAPABILITY_PROCESS_INSPECT,
    CAPABILITY_PROCESS_LAUNCH,
    CAPABILITY_PROCESS_TERMINATE,
    CAPABILITY_SHELL_EXECUTE,
    CAPABILITY_SYSTEM_BRIGHTNESS,
    CAPABILITY_SYSTEM_INFO,
    CAPABILITY_SYSTEM_VOLUME,
    CAPABILITY_UI_INSPECT,
    CAPABILITY_UI_INTERACT,
    CAPABILITY_WINDOW_CLOSE,
    CAPABILITY_WINDOW_FOCUS,
    CAPABILITY_WINDOW_LIST,
)
from jarvis.telemetry import logger


class WindowsNode:
    """Native Windows Host Node managing system capabilities."""

    def __init__(self) -> None:
        self._registered = False

    def register_capabilities(self) -> None:
        """Register all native Windows handlers into the canonical capability registry."""
        if self._registered:
            return

        logger.info("Registering Windows Node native execution capabilities...")

        # Filesystem
        capability_registry.register(
            CAPABILITY_FILESYSTEM_READ,
            lambda req: read_file(
                path_str=str(req.arguments["path"]),
                max_bytes=int(req.arguments.get("max_bytes", 1_000_000)),
            ),
        )
        capability_registry.register(
            CAPABILITY_FILESYSTEM_WRITE,
            lambda req: write_file(
                path_str=str(req.arguments["path"]),
                content=str(req.arguments["content"]),
                overwrite=bool(req.arguments.get("overwrite", True)),
            ),
        )
        capability_registry.register(
            CAPABILITY_FILESYSTEM_LIST,
            lambda req: list_directory(
                dir_str=str(req.arguments["path"]),
                max_entries=int(req.arguments.get("max_entries", 100)),
            ),
        )
        capability_registry.register(
            CAPABILITY_FILESYSTEM_DELETE,
            lambda req: delete_file(path_str=str(req.arguments["path"])),
        )

        # Process
        capability_registry.register(
            CAPABILITY_PROCESS_ENUMERATE,
            lambda req: enumerate_processes(
                limit=int(req.arguments.get("limit", 50)),
                filter_name=req.arguments.get("filter_name"),
            ),
        )
        capability_registry.register(
            CAPABILITY_PROCESS_INSPECT,
            lambda req: inspect_process(pid=int(req.arguments["pid"])),
        )
        capability_registry.register(
            CAPABILITY_PROCESS_LAUNCH,
            lambda req: launch_process(
                executable=str(req.arguments["executable"]),
                arguments=req.arguments.get("arguments"),
            ),
        )
        capability_registry.register(
            CAPABILITY_PROCESS_TERMINATE,
            lambda req: terminate_process(
                pid=int(req.arguments["pid"]),
                force=bool(req.arguments.get("force", False)),
            ),
        )

        # Shell
        capability_registry.register(
            CAPABILITY_SHELL_EXECUTE,
            lambda req: run_shell_command(
                command=str(req.arguments["command"]),
                shell=str(req.arguments.get("shell", "powershell")),
                timeout_seconds=float(req.arguments.get("timeout_seconds", 60.0)),
                cwd=req.arguments.get("cwd"),
            ),
        )

        # Desktop
        capability_registry.register(
            CAPABILITY_COMPUTER_SCREENSHOT,
            lambda req: capture_screenshot(),
        )
        capability_registry.register(
            CAPABILITY_COMPUTER_CLICK,
            lambda req: mouse_click(
                x=int(req.arguments["x"]),
                y=int(req.arguments["y"]),
                button=str(req.arguments.get("button", "left")),
                clicks=int(req.arguments.get("clicks", 1)),
            ),
        )
        capability_registry.register(
            CAPABILITY_COMPUTER_TYPE,
            lambda req: type_text(
                text=str(req.arguments["text"]),
                interval=float(req.arguments.get("interval", 0.02)),
            ),
        )
        capability_registry.register(
            CAPABILITY_COMPUTER_KEY,
            lambda req: press_key(key=str(req.arguments["key"])),
        )

        # System
        capability_registry.register(
            CAPABILITY_SYSTEM_INFO,
            lambda req: get_system_info(),
        )
        capability_registry.register(
            CAPABILITY_SYSTEM_VOLUME,
            lambda req: (
                set_system_volume(int(req.arguments["volume_percent"]))
                if "volume_percent" in req.arguments
                else get_system_volume()
            ),
        )
        capability_registry.register(
            CAPABILITY_SYSTEM_BRIGHTNESS,
            lambda req: (
                set_display_brightness(int(req.arguments["brightness_percent"]))
                if "brightness_percent" in req.arguments
                else get_display_brightness()
            ),
        )

        # Application & Window Control
        from jarvis.core.app_control_engine import app_control_engine

        capability_registry.register(
            CAPABILITY_APP_LAUNCH,
            lambda req: app_control_engine.launch_application(
                target=str(req.arguments["target"]),
                arguments=req.arguments.get("arguments"),
                timeout_seconds=float(req.arguments.get("timeout_seconds", 6.0)),
            ).to_dict(),
        )
        capability_registry.register(
            CAPABILITY_WINDOW_FOCUS,
            lambda req: app_control_engine.focus_window(
                target=str(req.arguments["target"])
            ).to_dict(),
        )
        capability_registry.register(
            CAPABILITY_WINDOW_CLOSE,
            lambda req: app_control_engine.close_window(
                target=str(req.arguments["target"])
            ).to_dict(),
        )
        capability_registry.register(
            CAPABILITY_WINDOW_LIST,
            lambda req: app_control_engine.list_windows(),
        )

        # UI Automation & In-App Interaction
        capability_registry.register(
            CAPABILITY_UI_INSPECT,
            lambda req: app_control_engine.inspect_ui(
                window_target=req.arguments.get("window_target"),
                max_depth=int(req.arguments.get("max_depth", 5)),
                max_elements=int(req.arguments.get("max_elements", 150)),
            ),
        )
        capability_registry.register(
            CAPABILITY_UI_INTERACT,
            lambda req: app_control_engine.interact(
                window_target=str(req.arguments["window_target"]),
                element_query=str(req.arguments["element_query"]),
                action=str(req.arguments.get("action", "click")),
                value=req.arguments.get("value"),
            ).to_dict(),
        )
        capability_registry.register(
            CAPABILITY_COMPUTER_ACT,
            lambda req: app_control_engine.execute_computer_action(
                ComputerActParams(**req.arguments)
            ).to_dict(),
        )

        self._registered = True
        logger.info("Windows Node capabilities successfully registered.")


# Global singleton instance
windows_node = WindowsNode()
