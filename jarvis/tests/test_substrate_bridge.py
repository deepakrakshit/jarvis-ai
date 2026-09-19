"""Automated Test Suite for JARVIS OpenClaw Substrate Bridge.

Verifies:
- SubstrateBridge initialization and dynamic runtime resolution
- Synchronous and asynchronous capability descriptor retrieval
- OpenClaw CUA computer.act dispatch via Node runner
- Stdin/stdout JSON-RPC daemon lifecycle
- Error isolation and protocol validation
"""

import pytest

from jarvis.execution.substrate_bridge import (
    SubstrateBridge,
    SubstrateBridgeError,
    substrate_bridge,
)


def test_substrate_bridge_initialization() -> None:
    """Verify SubstrateBridge resolves workspace and runner paths dynamically."""
    bridge = SubstrateBridge()
    assert bridge._workspace_dir.exists()
    assert bridge._runner_path.exists()
    assert "openclaw_substrate_runner.ts" in str(bridge._runner_path)
    assert not bridge.is_running


def test_substrate_bridge_capabilities_sync() -> None:
    """Verify synchronous capability discovery matches OpenClaw CUA contract."""
    caps = substrate_bridge.get_capabilities_sync()
    assert isinstance(caps, dict)
    assert caps.get("contractVersion") == 2

    provider = caps.get("provider", {})
    assert provider.get("id") == "cua-computer"
    assert "cua-computer-v2:" in provider.get("generation", "")

    actions = caps.get("actions", [])
    assert isinstance(actions, list)
    assert "screenshot" in actions
    assert "left_click" in actions
    assert "list_windows" in actions
    assert "type" in actions
    assert "key" in actions


def test_substrate_bridge_health_sync() -> None:
    """Verify health check probe against the substrate runner."""
    healthy = substrate_bridge.is_healthy_sync()
    assert healthy is True


def test_substrate_bridge_act_list_windows_sync() -> None:
    """Verify computer.act list_windows dispatches through OpenClaw runner."""
    res = substrate_bridge.execute_act_sync("list_windows")
    assert isinstance(res, dict)
    assert res.get("ok") is True
    assert "details" in res
    assert "windows" in res["details"]
    assert isinstance(res["details"]["windows"], list)


def test_substrate_bridge_act_cursor_sync() -> None:
    """Verify computer.act get_cursor_position returns coordinates."""
    res = substrate_bridge.execute_act_sync("get_cursor_position")
    assert isinstance(res, dict)
    assert res.get("ok") is True
    assert "details" in res
    assert "x" in res["details"]
    assert "y" in res["details"]


def test_substrate_bridge_act_unsupported_action() -> None:
    """Verify invalid action returns proper error diagnostics."""
    with pytest.raises(SubstrateBridgeError) as exc_info:
        substrate_bridge.execute_act_sync("__invalid_unknown_action__")
    assert "COMPUTER_UNSUPPORTED_ACTION" in str(exc_info.value) or "Substrate" in str(
        exc_info.value
    )


@pytest.mark.asyncio
async def test_substrate_bridge_daemon_lifecycle() -> None:
    """Verify asynchronous JSON-RPC daemon mode startup, communication, and shutdown."""
    bridge = SubstrateBridge()
    try:
        await bridge.start()
        assert bridge.is_running

        # Health probe over JSON-RPC IPC
        healthy = await bridge.is_healthy()
        assert healthy is True

        # Query capabilities over JSON-RPC IPC
        caps = await bridge.get_capabilities()
        assert caps.get("contractVersion") == 2
        assert caps.get("provider", {}).get("id") == "cua-computer"

        # Execute action over JSON-RPC IPC
        act_res = await bridge.execute_act("get_cursor_position")
        assert act_res.get("ok") is True
        assert "details" in act_res
    finally:
        await bridge.stop()
        assert not bridge.is_running
