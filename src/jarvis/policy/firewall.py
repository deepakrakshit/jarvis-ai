"""Capability Firewall and Canonical Action Rules for JARVIS.

Core Invariant: Every executable capability has a canonical identifier.
The model receives only permitted capabilities and cannot execute arbitrary
unrestricted host primitives.
"""

from typing import Dict, Set

from jarvis.contracts.action import RiskTier

# Canonical capability identifiers
CAPABILITY_FILESYSTEM_READ = "filesystem.read"
CAPABILITY_FILESYSTEM_WRITE = "filesystem.write"
CAPABILITY_FILESYSTEM_DELETE = "filesystem.delete"
CAPABILITY_FILESYSTEM_LIST = "filesystem.list"
CAPABILITY_FILESYSTEM_SEARCH = "filesystem.search"

CAPABILITY_PROCESS_ENUMERATE = "process.enumerate"
CAPABILITY_PROCESS_LAUNCH = "process.launch"
CAPABILITY_PROCESS_TERMINATE = "process.terminate"
CAPABILITY_PROCESS_INSPECT = "process.inspect"

CAPABILITY_SHELL_EXECUTE = "shell.execute"

CAPABILITY_COMPUTER_SCREENSHOT = "computer.screenshot"
CAPABILITY_COMPUTER_CLICK = "computer.click"
CAPABILITY_COMPUTER_TYPE = "computer.type"
CAPABILITY_COMPUTER_KEY = "computer.key"
CAPABILITY_COMPUTER_ACT = "computer.act"

CAPABILITY_APP_LAUNCH = "app.launch"
CAPABILITY_WINDOW_FOCUS = "window.focus"
CAPABILITY_WINDOW_CLOSE = "window.close"
CAPABILITY_WINDOW_LIST = "window.list"
CAPABILITY_UI_INSPECT = "ui.inspect"
CAPABILITY_UI_INTERACT = "ui.interact"

CAPABILITY_SYSTEM_VOLUME = "system.volume"
CAPABILITY_SYSTEM_BRIGHTNESS = "system.brightness"
CAPABILITY_SYSTEM_INFO = "system.info"

CAPABILITY_BROWSER_NAVIGATE = "browser.navigate"
CAPABILITY_BROWSER_SNAPSHOT = "browser.snapshot"
CAPABILITY_BROWSER_CLICK = "browser.click"
CAPABILITY_BROWSER_TYPE = "browser.type"
CAPABILITY_BROWSER_SCREENSHOT = "browser.screenshot"

CAPABILITY_WHATSAPP_CALL = "whatsapp.call"
CAPABILITY_WHATSAPP_STATUS = "whatsapp.status"
CAPABILITY_WHATSAPP_HISTORY = "whatsapp.history"
CAPABILITY_WHATSAPP_LOGIN = "whatsapp.login"
CAPABILITY_WHATSAPP_LOGOUT = "whatsapp.logout"

# Risk tier assignments for each capability
CAPABILITY_RISK_MAP: Dict[str, RiskTier] = {
    CAPABILITY_FILESYSTEM_READ: RiskTier.READ_ONLY,
    CAPABILITY_FILESYSTEM_LIST: RiskTier.READ_ONLY,
    CAPABILITY_FILESYSTEM_SEARCH: RiskTier.READ_ONLY,
    CAPABILITY_FILESYSTEM_WRITE: RiskTier.MEDIUM,
    CAPABILITY_FILESYSTEM_DELETE: RiskTier.HIGH,
    CAPABILITY_PROCESS_ENUMERATE: RiskTier.READ_ONLY,
    CAPABILITY_PROCESS_INSPECT: RiskTier.READ_ONLY,
    CAPABILITY_PROCESS_LAUNCH: RiskTier.MEDIUM,
    CAPABILITY_PROCESS_TERMINATE: RiskTier.HIGH,
    CAPABILITY_SHELL_EXECUTE: RiskTier.HIGH,
    CAPABILITY_APP_LAUNCH: RiskTier.MEDIUM,
    CAPABILITY_WINDOW_FOCUS: RiskTier.LOW,
    CAPABILITY_WINDOW_CLOSE: RiskTier.MEDIUM,
    CAPABILITY_WINDOW_LIST: RiskTier.READ_ONLY,
    CAPABILITY_UI_INSPECT: RiskTier.READ_ONLY,
    CAPABILITY_UI_INTERACT: RiskTier.MEDIUM,
    CAPABILITY_COMPUTER_SCREENSHOT: RiskTier.READ_ONLY,
    CAPABILITY_COMPUTER_CLICK: RiskTier.MEDIUM,
    CAPABILITY_COMPUTER_TYPE: RiskTier.MEDIUM,
    CAPABILITY_COMPUTER_KEY: RiskTier.MEDIUM,
    CAPABILITY_COMPUTER_ACT: RiskTier.MEDIUM,
    CAPABILITY_SYSTEM_VOLUME: RiskTier.LOW,
    CAPABILITY_SYSTEM_BRIGHTNESS: RiskTier.LOW,
    CAPABILITY_SYSTEM_INFO: RiskTier.READ_ONLY,
    CAPABILITY_BROWSER_NAVIGATE: RiskTier.LOW,
    CAPABILITY_BROWSER_SNAPSHOT: RiskTier.READ_ONLY,
    CAPABILITY_BROWSER_CLICK: RiskTier.MEDIUM,
    CAPABILITY_BROWSER_TYPE: RiskTier.MEDIUM,
    CAPABILITY_BROWSER_SCREENSHOT: RiskTier.READ_ONLY,
    CAPABILITY_WHATSAPP_CALL: RiskTier.MEDIUM,
    CAPABILITY_WHATSAPP_STATUS: RiskTier.READ_ONLY,
    CAPABILITY_WHATSAPP_HISTORY: RiskTier.READ_ONLY,
    CAPABILITY_WHATSAPP_LOGIN: RiskTier.LOW,
    CAPABILITY_WHATSAPP_LOGOUT: RiskTier.MEDIUM,
}

# Blacklisted dangerous shell substrings that fail-closed immediately
DANGEROUS_SHELL_PATTERNS: Set[str] = {
    "format ",
    "rmdir /s /q c:",
    "del /f /s /q c:",
    ":(){ :|:& };:",
    "drop database",
    "truncate table",
    "diskpart",
    "bcdedit",
    "vssadmin delete shadows",
}


def is_dangerous_command(command: str) -> bool:
    """Check if a shell command matches strictly prohibited destructive patterns."""
    normalized = command.strip().lower()
    for pattern in DANGEROUS_SHELL_PATTERNS:
        if pattern in normalized:
            return True
    return False
