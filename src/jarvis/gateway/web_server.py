"""JARVIS Static Web UI Server.

Serves the Neural 3D Orb and Web Client interface over local HTTP.
Enables local secure-origin access for Web Speech API and microphone permissions.
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Any, Optional

from jarvis.config import settings
from jarvis.telemetry import logger


class JarvisUIRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler with CORS support, explicit MIME types, and quiet telemetry."""

    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".json": "application/json",
        ".wasm": "application/wasm",
        ".css": "text/css",
        ".html": "text/html; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".ico": "image/x-icon",
    }

    def end_headers(self) -> None:
        """Inject CORS and caching headers."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        """Handle CORS pre-flight requests."""
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Route access logs to debug telemetry rather than polluting the console."""
        logger.debug(f"UI HTTP: {self.address_string()} - {format % args}")


class JarvisWebServer:
    """Threaded HTTP server hosting the JARVIS static web interface."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        workspace_dir: Optional[Path] = None,
    ) -> None:
        self.host = host or settings.UI_HOST
        self.port = port or settings.UI_PORT
        self.workspace_dir = workspace_dir or settings.WORKSPACE_DIR
        self._server: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        """Check whether the web server is actively serving."""
        return self._is_running

    @property
    def url(self) -> str:
        """Get the base HTTP URL of the web server."""
        display_host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{display_host}:{self.port}"

    def start(self, open_browser: bool = False, gateway_port: Optional[int] = None) -> int:
        """Start the HTTP server on a background daemon thread with port discovery."""
        if self._is_running and self._server:
            return self.port

        target_port = self.port
        auto_discover = getattr(settings, "UI_PORT_AUTO_DISCOVERY", True)
        max_search = getattr(settings, "UI_PORT_SEARCH_LIMIT", 50) if auto_discover else 1

        handler_factory = functools.partial(
            JarvisUIRequestHandler, directory=str(self.workspace_dir)
        )

        for offset in range(max_search):
            attempt_port = target_port + offset
            try:
                self._server = http.server.ThreadingHTTPServer(
                    (self.host, attempt_port), handler_factory
                )
                self.port = attempt_port
                settings.UI_PORT = attempt_port
                break
            except OSError:
                continue

        if not self._server:
            raise RuntimeError(
                f"Failed to bind UI HTTP server on {self.host}:{target_port} "
                f"(tried {max_search} ports)."
            )

        self._is_running = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="jarvis-ui-http-server",
        )
        self._thread.start()
        logger.info(f"JARVIS Neural UI Server online at {self.url}")

        if open_browser:
            self.launch_browser(gateway_port=gateway_port)

        return self.port

    def launch_desktop_window(self, gateway_port: Optional[int] = None) -> None:
        """Launch JARVIS in a dedicated, borderless desktop application window."""
        target_url = f"{self.url}/index.html"
        if gateway_port:
            target_url += f"?gw={gateway_port}"

        # 1. Search for Microsoft Edge (native on Windows)
        edge_candidates = [
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
            / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("LocalAppData", "")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        # 2. Search for Google Chrome
        chrome_candidates = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LocalAppData", "")) / "Google/Chrome/Application/chrome.exe",
        ]

        app_browser_exe: Optional[Path] = None
        for candidate in edge_candidates + chrome_candidates:
            if candidate.exists():
                app_browser_exe = candidate
                break

        if not app_browser_exe:
            which_edge = shutil.which("msedge")
            which_chrome = shutil.which("chrome")
            if which_edge:
                app_browser_exe = Path(which_edge)
            elif which_chrome:
                app_browser_exe = Path(which_chrome)

        if app_browser_exe:
            try:
                logger.info(f"Launching JARVIS dedicated app window via {app_browser_exe.name}...")
                subprocess.Popen(
                    [
                        str(app_browser_exe),
                        f"--app={target_url}",
                        "--window-size=1080,840",
                        "--window-position=center",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception as app_err:
                logger.debug(f"Direct app window launch error: {app_err}")

        # Fallback to default browser if standalone app mode is unavailable
        try:
            logger.info(f"Opening browser to {target_url}...")
            webbrowser.open(target_url)
        except Exception as err:
            logger.warning(f"Could not open application window automatically: {err}")

    def launch_browser(self, gateway_port: Optional[int] = None) -> None:
        """Alias for launch_desktop_window for backwards compatibility."""
        self.launch_desktop_window(gateway_port=gateway_port)

    def stop(self) -> None:
        """Gracefully stop the HTTP server and release port."""
        if not self._is_running or not self._server:
            return

        self._is_running = False
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception as err:
            logger.debug(f"UI Server shutdown note: {err}")
        finally:
            self._server = None
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            self._thread = None
            logger.info("JARVIS Neural UI Server stopped cleanly.")
