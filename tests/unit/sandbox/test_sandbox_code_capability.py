"""Unit tests for the canonical sandbox:code:execute capability."""

from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.capabilities.builtin import BUILTIN_CAPABILITIES
from jarvis.sandbox.manager import SandboxLifecycleManager
from jarvis.sandbox.provider import TestDoubleSandboxProvider
from jarvis.sandbox.registry import SandboxProviderRegistry
from jarvis.tools.native import dispatch_native_tool
from jarvis.tools.native.sandbox_code import execute_in_sandbox


def test_builtin_manifest_registration() -> None:
    """Verify sandbox:code:execute is registered with correct security attributes."""
    manifest = next(
        (m for m in BUILTIN_CAPABILITIES if m.capability_id == "sandbox:code:execute"), None
    )
    assert manifest is not None
    assert manifest.tool_type.value == "SANDBOX"
    assert "code:execute" in manifest.required_scopes
    assert "sandbox:execute" in manifest.required_scopes
    assert manifest.sandbox_requirement is True


@pytest.mark.asyncio
async def test_execute_in_sandbox_with_test_double() -> None:
    """Verify execute_in_sandbox end-to-end lifecycle using a test double provider."""
    provider = TestDoubleSandboxProvider()
    registry = SandboxProviderRegistry()
    registry.register_provider(provider)
    mgr = SandboxLifecycleManager(registry=registry)

    result = await execute_in_sandbox(
        command=["pytest", "-v"],
        files={"tests/test_sample.py": "def test_pass(): assert True\n"},
        timeout_seconds=15.0,
        task_id=uuid4(),
        manager=mgr,
        allow_test_doubles=True,
    )

    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert result["isolation_tier"] == "TIER_1_CONTAINER"
    assert result["provider_category"] == "TEST_DOUBLE"
    assert result["sandbox_status"] == "TEST_DOUBLE"
    assert "sandbox_id" in result


@pytest.mark.asyncio
async def test_execute_in_sandbox_timeout_handling() -> None:
    """Verify execute_in_sandbox reports timeout properly without hanging."""
    provider = TestDoubleSandboxProvider(simulate_timeout=True)
    registry = SandboxProviderRegistry()
    registry.register_provider(provider)
    mgr = SandboxLifecycleManager(registry=registry)

    result = await execute_in_sandbox(
        command=["python", "-c", "import time; time.sleep(100)"],
        timeout_seconds=2.0,
        manager=mgr,
        allow_test_doubles=True,
    )

    assert result["timed_out"] is True
    assert result["exit_code"] == -1
    assert result["sandbox_status"] == "TIMED_OUT"
    assert "timed out" in result["stderr"]


@pytest.mark.asyncio
async def test_dispatch_native_tool_sandbox_execution() -> None:
    """Verify dispatch_native_tool routes sandbox:code:execute correctly."""
    res = await dispatch_native_tool(
        tool_id="sandbox:code:execute",
        arguments={
            "command": ["pytest"],
            "timeout_seconds": 10.0,
            "allow_test_doubles": True,
        },
    )
    assert res["exit_code"] == 0
    assert "sandbox_id" in res
