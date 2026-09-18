"""Dedicated Failure Safety Test: Absolute Prohibition of Host Fallback.

Requirement 37:
When the sandbox provider is broken or unavailable:
1. Execution fails closed with SandboxBackendUnavailableError.
2. Under NO circumstance is the host runner (run_python_test / LocalProcessSandbox) invoked.
3. Explicit assertion that host execution was NOT called.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from jarvis.core.exceptions import SandboxBackendUnavailableError
from jarvis.sandbox.detector import get_system_capabilities
from jarvis.sandbox.models import CapabilityStatus
from jarvis.tools.native import dispatch_native_tool
from jarvis.tools.native.sandbox_code import execute_in_sandbox


@pytest.mark.asyncio
async def test_failure_safety_no_host_fallback_when_sandbox_broken() -> None:
    """Verify that when the sandbox backend is unavailable, execution fails closed

    and NEVER calls the host runner or executes host subprocesses.
    """
    # 1. Spy / mock the host execution facilities
    with (
        patch(
            "jarvis.tools.native.code.run_python_test", new_callable=AsyncMock
        ) as mock_host_runner,
        patch(
            "jarvis.sandbox.runner.LocalProcessSandbox.execute", new_callable=AsyncMock
        ) as mock_local_sandbox,
        patch(
            "jarvis.sandbox.runner.LocalProcessSandbox.execute_shell", new_callable=AsyncMock
        ) as mock_local_shell,
    ):
        # 2. Force daemon / sandbox backend to appear offline / unavailable
        caps = get_system_capabilities()
        disabled_caps = caps.model_copy(
            update={
                "docker_daemon_available": CapabilityStatus.UNAVAILABLE,
                "docker_cli_available": CapabilityStatus.UNAVAILABLE,
                "diagnostics": ("Simulated offline sandbox engine for failure safety test",),
            }
        )

        with (
            patch(
                "jarvis.tools.native.sandbox_code.get_system_capabilities",
                return_value=disabled_caps,
            ),
            patch("jarvis.sandbox.detector.get_system_capabilities", return_value=disabled_caps),
        ):
            # 3. Attempt direct invocation of sandbox code execution
            with pytest.raises(SandboxBackendUnavailableError) as exc_info:
                await execute_in_sandbox(
                    command=["pytest", "tests/unit/test_foo.py"],
                    timeout_seconds=10.0,
                    task_id=uuid4(),
                    allow_test_doubles=False,
                )

            assert "Fail-closed invariant triggered" in str(exc_info.value)
            assert "Automatic unisolated host execution fallback is strictly prohibited" in str(
                exc_info.value
            )

            # 4. Attempt invocation via central native tool dispatcher
            with pytest.raises(SandboxBackendUnavailableError) as disp_exc_info:
                await dispatch_native_tool(
                    tool_id="sandbox:code:execute",
                    arguments={
                        "command": ["python", "-c", "import os; print(os.getuid())"],
                        "timeout_seconds": 10.0,
                        "allow_test_doubles": False,
                    },
                )

            assert "Fail-closed invariant triggered" in str(disp_exc_info.value)

            # 5. STRICT INVARIANT ASSERTIONS: The host runner was NEVER invoked!
            mock_host_runner.assert_not_called()
            mock_local_sandbox.assert_not_called()
            mock_local_shell.assert_not_called()


@pytest.mark.asyncio
async def test_failure_safety_with_broken_provider_error() -> None:
    """Verify that if the sandbox provider encounters an unexpected crash during creation,

    it raises an error and does NOT silently fall back to host runner.
    """
    with (
        patch(
            "jarvis.tools.native.code.run_python_test", new_callable=AsyncMock
        ) as mock_host_runner,
        patch(
            "jarvis.sandbox.runner.LocalProcessSandbox.execute", new_callable=AsyncMock
        ) as mock_local_sandbox,
    ):
        from jarvis.sandbox.provider import TestDoubleSandboxProvider

        broken_provider = TestDoubleSandboxProvider(simulate_unavailable=True)
        from jarvis.sandbox.manager import SandboxLifecycleManager
        from jarvis.sandbox.registry import SandboxProviderRegistry

        registry = SandboxProviderRegistry()
        registry.register_provider(broken_provider)
        manager = SandboxLifecycleManager(registry=registry)

        with pytest.raises(SandboxBackendUnavailableError):
            await execute_in_sandbox(
                command=["pytest"],
                manager=manager,
                allow_test_doubles=True,
            )

        # Confirm host execution was not called
        mock_host_runner.assert_not_called()
        mock_local_sandbox.assert_not_called()
