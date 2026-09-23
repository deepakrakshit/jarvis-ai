"""Dynamic Application Registry and Discovery for Windows.

Discovers applications dynamically from Start Menu shortcuts, standard system tools,
registered URI schemes, and running processes without relying on static application catalogs.
"""

import os
import shutil
import winreg
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from jarvis.telemetry import logger


class ApplicationInfo:
    """Discovered application identity and execution metadata."""

    def __init__(
        self,
        name: str,
        executable_path: Optional[str] = None,
        uri_scheme: Optional[str] = None,
        app_id: Optional[str] = None,
        source: str = "discovered",
    ) -> None:
        self.name = name
        self.executable_path = executable_path
        self.uri_scheme = uri_scheme
        self.app_id = app_id
        self.source = source

    def to_dict(self) -> Dict[str, Any]:
        """Return serializable dictionary representation."""
        return {
            "name": self.name,
            "executable_path": self.executable_path,
            "uri_scheme": self.uri_scheme,
            "app_id": self.app_id,
            "source": self.source,
        }

    def __repr__(self) -> str:
        return f"<ApplicationInfo name='{self.name}' exe='{self.executable_path}' uri='{self.uri_scheme}'>"


class AppRegistry:
    """Dynamically registers, discovers, and resolves applications on the host system."""

    def __init__(self) -> None:
        self._cache: Dict[str, ApplicationInfo] = {}

    def discover_installed_apps(self, force_refresh: bool = False) -> List[ApplicationInfo]:
        """Discover all installed apps (alias for discover_applications)."""
        return self.discover_applications(refresh=force_refresh)

    def discover_applications(self, refresh: bool = False) -> List[ApplicationInfo]:
        """Enumerate all discoverable applications on the Windows host."""
        if self._cache and not refresh:
            return list(self._cache.values())

        discovered: Dict[str, ApplicationInfo] = {}

        # 1. Standard System Tools dynamically discovered via PATH / SystemRoot
        system_tools = [
            "notepad",
            "mspaint",
            "calc",
            "explorer",
            "cmd",
            "powershell",
            "taskmgr",
            "control",
            "regedit",
            "ms-settings",
        ]
        for tool in system_tools:
            resolved = shutil.which(tool)
            if resolved:
                name = Path(resolved).stem.capitalize()
                discovered[name.lower()] = ApplicationInfo(
                    name=name,
                    executable_path=resolved,
                    source="system_path",
                )

        # 2. Discover applications from Windows Start Menu folders
        start_menu_paths: List[Path] = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            start_menu_paths.append(
                Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            )
        programdata = os.environ.get("PROGRAMDATA")
        if programdata:
            start_menu_paths.append(
                Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            )

        for folder in start_menu_paths:
            if not folder.exists():
                continue
            try:
                for root, _, files in os.walk(folder):
                    for file in files:
                        if file.lower().endswith(".lnk"):
                            shortcut_path = Path(root) / file
                            app_name = shortcut_path.stem.strip()
                            if app_name and app_name.lower() not in discovered:
                                discovered[app_name.lower()] = ApplicationInfo(
                                    name=app_name,
                                    executable_path=str(shortcut_path),
                                    source="start_menu",
                                )
            except Exception as err:
                logger.debug(f"Error scanning Start Menu in {folder}: {err}")

        # 3. Discover from registered URI protocols in HKEY_CLASSES_ROOT
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "") as root_key:
                num_subkeys, _, _ = winreg.QueryInfoKey(root_key)
                for i in range(min(num_subkeys, 500)):
                    try:
                        subkey_name = winreg.EnumKey(root_key, i)
                        if not subkey_name.startswith((".", "{", "_")):
                            with winreg.OpenKey(root_key, subkey_name) as sub:
                                try:
                                    val, _ = winreg.QueryValueEx(sub, "URL Protocol")
                                    protocol = f"{subkey_name}:"
                                    name = subkey_name.capitalize()
                                    if name.lower() not in discovered:
                                        discovered[name.lower()] = ApplicationInfo(
                                            name=name,
                                            uri_scheme=protocol,
                                            source="registry_uri",
                                        )
                                except (OSError, FileNotFoundError):
                                    pass
                    except OSError:
                        pass
        except Exception as err:
            logger.debug(f"Registry URI discovery error: {err}")

        # 4. Discover currently running processes with active executables
        try:
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    info = proc.info
                    exe = info.get("exe")
                    pname = info.get("name")
                    if exe and pname:
                        clean_name = Path(exe).stem.strip()
                        if clean_name and clean_name.lower() not in discovered:
                            discovered[clean_name.lower()] = ApplicationInfo(
                                name=clean_name,
                                executable_path=exe,
                                source="running_process",
                            )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as err:
            logger.debug(f"Process discovery error: {err}")

        self._cache = discovered
        logger.info(f"AppRegistry discovered {len(discovered)} applications dynamically.")
        return list(discovered.values())

    def find_application(self, query: str) -> Optional[ApplicationInfo]:
        """Find the best matching application by query string."""
        if not self._cache:
            self.discover_applications()

        q = query.strip().lower()

        # 1. Exact match
        if q in self._cache:
            return self._cache[q]

        # 2. Match with .exe stripped or added
        clean_q = q.replace(".exe", "")
        if clean_q in self._cache:
            return self._cache[clean_q]

        # 3. Substring matching
        for key, app in self._cache.items():
            if clean_q == key or clean_q in key or key in clean_q:
                return app

        # 4. Direct check if query is an absolute path or in PATH
        resolved_path = shutil.which(query)
        if resolved_path:
            name = Path(resolved_path).stem.capitalize()
            app = ApplicationInfo(name=name, executable_path=resolved_path, source="direct_path")
            self._cache[name.lower()] = app
            return app

        return None


# Global singleton registry
app_registry = AppRegistry()
