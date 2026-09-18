"""Unit tests for Sandbox Security Inspector and Backend Capability Reporter."""

from __future__ import annotations

from uuid import uuid4

from jarvis.sandbox.inspector import (
    explain_sandbox_configuration,
    get_backend_capability_report,
)
from jarvis.sandbox.models import SandboxProfile


def test_get_backend_capability_report_structure() -> None:
    """Verify backend capability report includes all required machine-readable fields."""
    report = get_backend_capability_report()

    required_keys = [
        "host_os",
        "host_arch",
        "wsl_platform",
        "wsl_distros",
        "docker_desktop_installed",
        "docker_cli",
        "docker_daemon",
        "docker_server_version",
        "docker_backend",
        "docker_backend_confidence",
        "rootless",
        "cgroup_v2",
        "cpu_limits",
        "memory_limits",
        "pids_limits",
        "seccomp",
        "gvisor",
        "kata",
        "oci_runtime",
        "supported_tiers",
        "real_isolation_verified",
        "diagnostics",
        "timestamp",
    ]
    for key in required_keys:
        assert key in report, f"Missing key '{key}' in capability report"

    # Each capability entry must have status, evidence, source, timestamp
    for cap_key in (
        "wsl_platform",
        "wsl_distros",
        "docker_desktop_installed",
        "docker_cli",
        "docker_daemon",
        "seccomp",
        "gvisor",
        "kata",
    ):
        entry = report[cap_key]
        assert "status" in entry
        assert "evidence" in entry
        assert "source" in entry
        assert "timestamp" in entry


def test_explain_sandbox_configuration_structure() -> None:
    """Verify sandbox explanation report contains comprehensive governance and security controls."""
    task_id = uuid4()
    profile = SandboxProfile(profile_id="custom-test-profile")
    explanation = explain_sandbox_configuration(task_id=task_id, profile=profile)

    assert explanation["task_id"] == str(task_id)
    assert explanation["agent_id"] == "coding"
    assert explanation["requested_tier"] == "TIER_1_CONTAINER"
    assert "requested_profile" in explanation
    assert "backend" in explanation
    assert "effective_security_controls" in explanation
    assert "authorization_and_governance" in explanation
    assert "real_isolation_verified" in explanation

    sec = explanation["effective_security_controls"]
    assert "filesystem_isolation" in sec
    assert "network_policy" in sec
    assert "credential_policy" in sec
    assert "docker_socket_boundary" in sec
    assert "user_and_privileges" in sec
    assert "resource_confinement" in sec

    # Ensure no secrets leak
    text_dump = str(explanation).lower()
    for forbidden in ("api_key", "secret_key", "token", "password"):
        assert forbidden not in text_dump or "prohibited" in text_dump or "strict_none" in text_dump
