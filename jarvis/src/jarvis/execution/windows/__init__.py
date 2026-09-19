"""Windows Execution Body package export for JARVIS."""

from jarvis.execution.windows.desktop import (
    capture_screenshot,
    list_open_windows,
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
from jarvis.execution.windows.host import WindowsNode, windows_node
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

__all__ = [
    "WindowsNode",
    "windows_node",
    "run_shell_command",
    "read_file",
    "write_file",
    "list_directory",
    "delete_file",
    "enumerate_processes",
    "inspect_process",
    "launch_process",
    "terminate_process",
    "capture_screenshot",
    "mouse_click",
    "type_text",
    "press_key",
    "list_open_windows",
    "get_system_info",
    "get_system_volume",
    "set_system_volume",
    "get_display_brightness",
    "set_display_brightness",
]
