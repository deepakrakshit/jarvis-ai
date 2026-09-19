"""Windows WindowStation and Desktop Attachment Utilities.

Ensures that background and CLI threads are properly attached to the interactive
user desktop (WinSta0\\default) so that native Win32 window APIs and Windows UI Automation
can enumerate, inspect, and interact with open desktop applications.
"""

import ctypes

from jarvis.telemetry import logger

user32 = ctypes.windll.user32

WINSTA_ALL = 0x37F
DESKTOP_ALL = 0x01FF

_desktop_attached = False


def ensure_interactive_desktop_attached() -> bool:
    """Attach the calling thread to the interactive user desktop WinSta0\\default."""
    global _desktop_attached
    if _desktop_attached:
        return True

    try:
        hwinsta = user32.OpenWindowStationW("WinSta0", False, WINSTA_ALL)
        if hwinsta:
            user32.SetProcessWindowStation(hwinsta)

        hdesk = user32.OpenDesktopW("default", 0, False, DESKTOP_ALL)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
            _desktop_attached = True
            logger.debug("Successfully attached thread to interactive desktop (WinSta0\\default)")
            return True
    except Exception as err:
        logger.debug(f"Could not attach to WinSta0\\default: {err}")

    return False
