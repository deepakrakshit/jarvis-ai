"""Auxiliary Native Sidecar Process Management.

Implements Sections 8 and 40 of ARCHITECTURE.md:
- Management of high-throughput native node-host and helper sidecars
- Process lifecycle tracking, health probes, and graceful termination
"""

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.telemetry import logger


class SidecarProcess:
    """Represents a running external native sidecar instance."""

    def __init__(
        self,
        name: str,
        executable: str,
        args: List[str],
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> None:
        self.name = name
        self.executable = executable
        self.args = args
        self.cwd = cwd
        self.env = env
        self.process: Optional[subprocess.Popen[str]] = None

    def start(self) -> bool:
        """Launch the sidecar subprocess."""
        if self.is_running():
            logger.info(f"Sidecar '{self.name}' is already running.")
            return True

        cmd = [self.executable] + self.args
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.cwd) if self.cwd else None,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            logger.info(f"Started sidecar '{self.name}' (PID: {self.process.pid})")
            return True
        except Exception as err:
            logger.error(f"Failed to start sidecar '{self.name}': {err}")
            return False

    def is_running(self) -> bool:
        """Check if sidecar process is active."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def stop(self, timeout_seconds: float = 5.0) -> bool:
        """Gracefully terminate the sidecar process."""
        if not self.is_running() or self.process is None:
            return True

        logger.info(f"Terminating sidecar '{self.name}' (PID: {self.process.pid})...")
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout_seconds)
            logger.info(f"Sidecar '{self.name}' terminated successfully.")
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"Sidecar '{self.name}' did not exit cleanly; killing forcefully.")
            self.process.kill()
            self.process.wait(timeout=2.0)
            return True
        except Exception as err:
            logger.error(f"Error terminating sidecar '{self.name}': {err}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic health status."""
        running = self.is_running()
        return {
            "name": self.name,
            "running": running,
            "pid": self.process.pid if (running and self.process) else None,
            "exit_code": self.process.returncode if self.process else None,
            "executable": self.executable,
        }


class SidecarManager:
    """Registry and coordinator for active native sidecars."""

    def __init__(self) -> None:
        self._sidecars: Dict[str, SidecarProcess] = {}

    def register(
        self,
        name: str,
        executable: str,
        args: Optional[List[str]] = None,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> SidecarProcess:
        """Register and configure a sidecar process."""
        sidecar = SidecarProcess(
            name=name,
            executable=executable,
            args=args or [],
            cwd=cwd,
            env=env,
        )
        self._sidecars[name] = sidecar
        return sidecar

    def start(self, name: str) -> bool:
        """Start a registered sidecar."""
        sidecar = self._sidecars.get(name)
        if not sidecar:
            logger.warning(f"Sidecar '{name}' is not registered.")
            return False
        return sidecar.start()

    def stop(self, name: str) -> bool:
        """Stop an active sidecar."""
        sidecar = self._sidecars.get(name)
        if not sidecar:
            return False
        return sidecar.stop()

    def list_sidecars(self) -> List[Dict[str, Any]]:
        """Return health summaries for all registered sidecars."""
        return [s.get_status() for s in self._sidecars.values()]

    def stop_all(self) -> None:
        """Stop all running sidecars."""
        for sidecar in self._sidecars.values():
            if sidecar.is_running():
                sidecar.stop()


# Default singleton instance
sidecar_manager = SidecarManager()
