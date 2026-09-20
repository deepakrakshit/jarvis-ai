"""JARVIS OpenClaw Substrate Bridge.

Provides bidirectional IPC communication between the JARVIS Python Control Plane
and the native OpenClaw Node.js execution substrate runner.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SubstrateBridgeError(RuntimeError):
    """Raised when an interaction with the OpenClaw execution substrate fails."""


class SubstrateBridge:
    """Manages the OpenClaw execution substrate runner process and provides IPC execution."""

    def __init__(
        self,
        runner_path: Path | None = None,
        node_binary: str | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        if workspace_dir is not None:
            self._workspace_dir = workspace_dir
        else:
            # Dynamically resolve workspace root: src/jarvis/execution/substrate_bridge.py -> jarvis
            self._workspace_dir = Path(__file__).resolve().parent.parent.parent.parent

        if runner_path is not None:
            self._runner_path = runner_path
        else:
            env_runner = os.getenv("JARVIS_SUBSTRATE_RUNNER_PATH")
            if env_runner:
                self._runner_path = Path(env_runner)
            else:
                self._runner_path = (
                    self._workspace_dir / "substrate" / "runner" / "openclaw_substrate_runner.ts"
                )

        self._node_binary = (
            node_binary or os.getenv("JARVIS_NODE_BIN") or shutil.which("node") or "node"
        )
        self._npx_binary = shutil.which("npx") or "npx"

        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._pending_requests: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._is_shutting_down = False

    @property
    def is_running(self) -> bool:
        """Indicates whether the substrate daemon process is currently active."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Spawns the OpenClaw substrate runner daemon process with JSON-RPC IPC."""
        async with self._lock:
            if self.is_running:
                return

            if not self._runner_path.exists():
                raise SubstrateBridgeError(
                    f"Substrate runner file not found at {self._runner_path}"
                )

            cmd = [
                self._npx_binary,
                "--yes",
                "tsx",
                str(self._runner_path),
                "--daemon",
            ]

            logger.info("Starting OpenClaw Substrate Runner daemon: %s", " ".join(cmd))
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self._workspace_dir),
                    limit=20 * 1024 * 1024,
                )
            except Exception as exc:
                logger.error("Failed to spawn OpenClaw substrate daemon: %s", exc)
                raise SubstrateBridgeError(f"Substrate startup failure: {exc}") from exc

            self._is_shutting_down = False
            self._reader_task = asyncio.create_task(self._reader_loop())
            logger.info("OpenClaw Substrate Runner daemon started (pid=%s)", self._process.pid)

    async def stop(self) -> None:
        """Terminates the OpenClaw substrate runner daemon gracefully."""
        async with self._lock:
            self._is_shutting_down = True
            if self._reader_task and not self._reader_task.done():
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._reader_task = None

            if self._process is not None:
                if self._process.stdin is not None:
                    try:
                        self._process.stdin.close()
                        await self._process.stdin.wait_closed()
                    except Exception:
                        pass
                try:
                    self._process.terminate()
                    await asyncio.wait_for(self._process.wait(), timeout=3.0)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
                self._process = None

            # Fail any remaining pending futures
            for future in self._pending_requests.values():
                if not future.done():
                    future.set_exception(SubstrateBridgeError("Substrate process terminated"))
            self._pending_requests.clear()
            logger.info("OpenClaw Substrate Runner daemon stopped")

    async def _reader_loop(self) -> None:
        """Background loop reading JSON-RPC responses from the substrate runner stdout."""
        if self._process is None or self._process.stdout is None:
            return

        try:
            while not self._is_shutting_down:
                line_bytes = await self._process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    payload = json.loads(line)
                    req_id = payload.get("id")
                    if req_id and req_id in self._pending_requests:
                        future = self._pending_requests.pop(req_id)
                        if not future.done():
                            if "error" in payload and payload["error"]:
                                future.set_exception(
                                    SubstrateBridgeError(
                                        payload["error"].get("message", "Unknown substrate error")
                                    )
                                )
                            else:
                                future.set_result(payload.get("result", {}))
                except json.JSONDecodeError:
                    logger.debug("Non-JSON substrate stdout: %s", line)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Error in substrate reader loop: %s", exc)

    async def call_method(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Calls a JSON-RPC method on the substrate runner, ensuring daemon liveness."""
        if not self.is_running:
            await self.start()

        assert self._process is not None
        assert self._process.stdin is not None

        req_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[req_id] = future

        request_body = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        serialized = json.dumps(request_body) + "\n"

        try:
            self._process.stdin.write(serialized.encode("utf-8"))
            await self._process.stdin.drain()
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending_requests.pop(req_id, None)
            raise SubstrateBridgeError(
                f"Substrate RPC call '{method}' timed out after {timeout}s"
            ) from exc
        except Exception as exc:
            self._pending_requests.pop(req_id, None)
            raise SubstrateBridgeError(f"Substrate RPC call '{method}' failed: {exc}") from exc

    def call_method_sync(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Calls a substrate method synchronously via one-shot CLI execution."""
        if not self._runner_path.exists():
            raise SubstrateBridgeError(f"Substrate runner file not found at {self._runner_path}")

        cmd = [
            self._npx_binary,
            "--yes",
            "tsx",
            str(self._runner_path),
            "--method",
            method,
            "--params",
            json.dumps(params or {}),
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self._workspace_dir),
            )
            if res.returncode != 0:
                raise SubstrateBridgeError(
                    f"Substrate one-shot call '{method}' failed with code {res.returncode}: {res.stderr}"
                )
            output = res.stdout.strip()
            return json.loads(output) if output else {}
        except subprocess.TimeoutExpired as exc:
            raise SubstrateBridgeError(
                f"Substrate synchronous call '{method}' timed out after {timeout}s"
            ) from exc
        except Exception as exc:
            raise SubstrateBridgeError(
                f"Substrate synchronous call '{method}' failed: {exc}"
            ) from exc

    async def is_healthy(self, timeout: float = 15.0) -> bool:
        """Performs a health check probe against the substrate runner asynchronously."""
        try:
            res = await self.call_method("health", timeout=timeout)
            return res.get("ok") is True
        except Exception:
            return False

    def is_healthy_sync(self) -> bool:
        """Performs a health check probe against the substrate runner synchronously."""
        try:
            res = self.call_method_sync("health", timeout=10.0)
            return res.get("ok") is True
        except Exception:
            return False

    async def get_capabilities(self) -> dict[str, Any]:
        """Queries the OpenClaw Computer Use capability descriptor asynchronously."""
        return await self.call_method("capabilities", timeout=10.0)

    def get_capabilities_sync(self) -> dict[str, Any]:
        """Queries the OpenClaw Computer Use capability descriptor synchronously."""
        return self.call_method_sync("capabilities", timeout=15.0)

    async def execute_act(
        self, action: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Dispatches an action through OpenClaw's CUA computer.act implementation asynchronously."""
        payload = {"action": action}
        if params:
            payload.update(params)
        return await self.call_method("computer.act", payload, timeout=60.0)

    def execute_act_sync(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Dispatches an action through OpenClaw's CUA computer.act implementation synchronously."""
        payload = {"action": action}
        if params:
            payload.update(params)
        return self.call_method_sync("computer.act", payload, timeout=30.0)

    async def take_snapshot(self, format: str = "jpeg", max_width: int = 1280) -> dict[str, Any]:
        """Captures a display snapshot via OpenClaw's screen.snapshot pipeline asynchronously."""
        payload = {"format": format, "maxWidth": max_width}
        return await self.call_method("screen.snapshot", payload, timeout=30.0)

    def take_snapshot_sync(self, format: str = "jpeg", max_width: int = 1280) -> dict[str, Any]:
        """Captures a display snapshot via OpenClaw's screen.snapshot pipeline synchronously."""
        payload = {"format": format, "maxWidth": max_width}
        return self.call_method_sync("screen.snapshot", payload, timeout=30.0)

    async def run_system(self, command: str) -> dict[str, Any]:
        """Executes a system shell command through the substrate pipeline asynchronously."""
        return await self.call_method("system.run", {"command": command}, timeout=60.0)

    def run_system_sync(self, command: str) -> dict[str, Any]:
        """Executes a system shell command through the substrate pipeline synchronously."""
        return self.call_method_sync("system.run", {"command": command}, timeout=60.0)

    async def repair_tool_call(self, text: str) -> dict[str, Any]:
        """Repairs and extracts plain text or malformed tool call blocks via OpenClaw substrate."""
        return await self.call_method("tool.repair", {"text": text}, timeout=15.0)

    def repair_tool_call_sync(self, text: str) -> dict[str, Any]:
        """Synchronously repairs and extracts plain text or malformed tool call blocks via OpenClaw substrate."""
        return self.call_method_sync("tool.repair", {"text": text}, timeout=15.0)


# Global singleton instance
substrate_bridge = SubstrateBridge()
