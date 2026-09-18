"""Application resolution pipeline for finding and validating Windows desktop executables."""

from __future__ import annotations

import os
import platform
import re
import shutil
import winreg
from pathlib import Path

from jarvis.computer.models import AppResolution, ResolutionStatus
from jarvis.computer.windows_api import WindowsAPI
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Common application name aliases and canonical executable stems
APP_CANONICAL_ALIASES: dict[str, list[str]] = {
    "chrome": ["chrome.exe", "google chrome"],
    "google chrome": ["chrome.exe", "chrome"],
    "edge": ["msedge.exe", "microsoft edge"],
    "msedge": ["msedge.exe", "microsoft edge", "edge"],
    "microsoft edge": ["msedge.exe", "edge"],
    "vscode": ["code.cmd", "code.exe", "visual studio code"],
    "code": ["code.cmd", "code.exe", "visual studio code", "vscode"],
    "visual studio code": ["code.cmd", "code.exe", "vscode", "code"],
    "notepad": ["notepad.exe"],
    "calc": ["calc.exe", "calculator"],
    "calculator": ["calc.exe", "calc"],
    "paint": ["mspaint.exe"],
    "mspaint": ["mspaint.exe"],
    "terminal": ["wt.exe", "powershell.exe", "cmd.exe"],
    "windows terminal": ["wt.exe"],
    "explorer": ["explorer.exe"],
    "file explorer": ["explorer.exe"],
}


class ApplicationResolver:
    """7-source resolution engine for locating and validating Windows desktop applications."""

    def __init__(self, windows_api: WindowsAPI | None = None) -> None:
        self.windows_api = windows_api or WindowsAPI()
        self.is_windows = platform.system() == "Windows"

    def resolve(
        self,
        app_name: str,
        prefer_running: bool = True,
    ) -> AppResolution:
        """Execute multi-source resolution pipeline for target application.

        Args:
            app_name: Target application name, alias, or executable string.
            prefer_running: If true and the app is already open with a visible window, return it.

        Returns:
            Structured AppResolution detailing path, status, and discovery source.
        """
        clean_name = app_name.strip()
        if not clean_name:
            return AppResolution(
                status=ResolutionStatus.NOT_FOUND,
                app_name=app_name,
                diagnostic_message="Application name cannot be empty.",
            )

        # Validate no malicious shell delimiters
        if not re.match(r"^[a-zA-Z0-9_\-\. :\\/]+$", clean_name):
            return AppResolution(
                status=ResolutionStatus.NOT_FOUND,
                app_name=clean_name,
                diagnostic_message=f"Application name contains invalid characters: '{clean_name}'.",
            )

        lower_name = clean_name.lower()
        alias_candidates = [clean_name]
        for alias, targets in APP_CANONICAL_ALIASES.items():
            if lower_name == alias or lower_name in targets:
                for t in targets:
                    if t not in alias_candidates:
                        alias_candidates.append(t)

        # Source 1: Check if already running with a visible window
        if prefer_running and self.is_windows:
            running_win = self.windows_api.find_window(clean_name)
            if running_win and running_win.is_visible:
                return AppResolution(
                    status=ResolutionStatus.FOUND,
                    app_name=clean_name,
                    resolved_path=running_win.process_name,
                    resolution_source="RUNNING_PROCESS",
                    existing_hwnd=running_win.hwnd,
                    diagnostic_message=f"Found already running visible window: '{running_win.title}' (HWND {running_win.hwnd}).",
                )

        if not self.is_windows:
            # POSIX fallback
            which_p = shutil.which(clean_name)
            if which_p:
                return AppResolution(
                    status=ResolutionStatus.FOUND,
                    app_name=clean_name,
                    resolved_path=which_p,
                    resolution_source="SYSTEM_PATH",
                )
            return AppResolution(
                status=ResolutionStatus.NOT_FOUND,
                app_name=clean_name,
                diagnostic_message=f"Application '{clean_name}' not found in PATH.",
            )

        # Source 2: Windows App Paths Registry
        for candidate in alias_candidates:
            stem = candidate.replace(".exe", "").replace(".cmd", "")
            reg_path = self._query_app_paths_registry(stem)
            if reg_path:
                if "WindowsApps" in reg_path:
                    which_fallback = shutil.which(candidate)
                    if which_fallback:
                        reg_path = which_fallback
                return AppResolution(
                    status=ResolutionStatus.FOUND,
                    app_name=clean_name,
                    resolved_path=reg_path,
                    resolution_source="REGISTRY_APP_PATHS",
                    diagnostic_message=f"Resolved via Windows App Paths registry: '{reg_path}'.",
                )

        # Source 3: Start Menu Shortcuts (.lnk files)
        for candidate in alias_candidates:
            stem = candidate.replace(".exe", "").replace(".cmd", "")
            lnk_path = self._search_start_menu_shortcuts(stem)
            if lnk_path:
                return AppResolution(
                    status=ResolutionStatus.FOUND,
                    app_name=clean_name,
                    resolved_path=str(lnk_path),
                    resolution_source="START_MENU_SHORTCUT",
                    diagnostic_message=f"Resolved via Start Menu shortcut: '{lnk_path}'.",
                )

        # Source 4: Known Installed Directories
        for candidate in alias_candidates:
            known_path = self._search_known_directories(candidate)
            if known_path:
                return AppResolution(
                    status=ResolutionStatus.FOUND,
                    app_name=clean_name,
                    resolved_path=known_path,
                    resolution_source="KNOWN_DIRECTORIES",
                    diagnostic_message=f"Resolved via standard install directory: '{known_path}'.",
                )

        # Source 5: System PATH via shutil.which
        for candidate in alias_candidates:
            which_p = shutil.which(candidate)
            if which_p and os.path.exists(which_p):
                return AppResolution(
                    status=ResolutionStatus.FOUND,
                    app_name=clean_name,
                    resolved_path=which_p,
                    resolution_source="SYSTEM_PATH",
                    diagnostic_message=f"Resolved via system PATH: '{which_p}'.",
                )

        # Source 6: Direct file path
        if os.path.exists(clean_name):
            return AppResolution(
                status=ResolutionStatus.FOUND,
                app_name=clean_name,
                resolved_path=os.path.abspath(clean_name),
                resolution_source="DIRECT_PATH",
                diagnostic_message=f"Direct file path exists: '{clean_name}'.",
            )

        return AppResolution(
            status=ResolutionStatus.NOT_FOUND,
            app_name=clean_name,
            diagnostic_message=f"Could not resolve application '{clean_name}' across running processes, registry App Paths, Start Menu, or system PATH.",
        )

    def _query_app_paths_registry(self, stem: str) -> str | None:
        """Query HKLM and HKCU App Paths registry for executable location."""
        subkeys = [
            f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{stem}.exe",
            f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{stem}",
        ]
        roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
        views = [winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ | winreg.KEY_WOW64_32KEY]

        for root in roots:
            for subkey in subkeys:
                for view in views:
                    try:
                        with winreg.OpenKey(root, subkey, 0, view) as k:
                            val, _ = winreg.QueryValueEx(k, "")
                            cleaned = str(val).strip('"')
                            if cleaned and os.path.exists(cleaned):
                                return str(cleaned)
                    except Exception:
                        pass
        return None

    def _search_start_menu_shortcuts(self, query: str) -> Path | None:
        """Search Start Menu directories for matching .lnk shortcuts."""
        dirs = [
            Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
            Path(os.environ.get("APPDATA", ""))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
        ]
        q_lower = query.lower()

        # Exact stem match first
        for d in dirs:
            if not d.exists():
                continue
            for lnk in d.rglob("*.lnk"):
                if lnk.stem.lower() == q_lower:
                    return lnk

        # Substring stem match
        for d in dirs:
            if not d.exists():
                continue
            for lnk in d.rglob("*.lnk"):
                if q_lower in lnk.stem.lower():
                    return lnk

        return None

    def _search_known_directories(self, name: str) -> str | None:
        """Check standard installation root locations."""
        prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", "")

        candidates: list[Path] = [
            Path(prog_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(prog_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(prog_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(prog_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(local_app_data) / "Programs" / "Microsoft VS Code" / "Code.exe",
            Path(local_app_data) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd",
            Path(prog_files) / "Microsoft VS Code" / "Code.exe",
            Path(os.environ.get("WINDIR", "C:\\Windows")) / "System32" / "notepad.exe",
            Path(os.environ.get("WINDIR", "C:\\Windows")) / "System32" / "calc.exe",
            Path(os.environ.get("WINDIR", "C:\\Windows")) / "explorer.exe",
        ]

        n_lower = name.lower().replace(".exe", "").replace(".cmd", "")
        for cand in candidates:
            if cand.exists() and n_lower in cand.name.lower():
                return str(cand)

        return None

    def list_installed_apps(self, limit: int = 100) -> list[dict[str, str]]:
        """List discoverable applications from Start Menu and App Paths."""
        discovered: dict[str, str] = {}

        # 1. Start Menu Shortcuts
        dirs = [
            Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
            Path(os.environ.get("APPDATA", ""))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
        ]
        for d in dirs:
            if not d.exists():
                continue
            for lnk in d.rglob("*.lnk"):
                name = lnk.stem
                if name.lower() not in (
                    "uninstall",
                    "license",
                    "readme",
                ) and not name.lower().startswith("uninstall"):
                    discovered[name] = str(lnk)
                    if len(discovered) >= limit:
                        break

        # 2. App Paths
        if len(discovered) < limit:
            for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    with winreg.OpenKey(
                        root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
                    ) as k:
                        subkeys_count, _, _ = winreg.QueryInfoKey(k)
                        for i in range(subkeys_count):
                            subkey_name = winreg.EnumKey(k, i)
                            with winreg.OpenKey(k, subkey_name) as subk:
                                try:
                                    val, _ = winreg.QueryValueEx(subk, "")
                                    cleaned = val.strip('"')
                                    if cleaned and os.path.exists(cleaned):
                                        app_title = subkey_name.replace(".exe", "").capitalize()
                                        if app_title not in discovered:
                                            discovered[app_title] = cleaned
                                            if len(discovered) >= limit:
                                                break
                                except Exception:
                                    pass
                except Exception:
                    pass

        return [{"name": k, "path": v} for k, v in sorted(discovered.items())[:limit]]
