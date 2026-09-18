"""Unit tests for Sandbox Path Security Guard and Boundary Protection."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from jarvis.core.exceptions import SandboxSecurityViolationError
from jarvis.sandbox.models import SandboxProfile
from jarvis.sandbox.security import (
    is_unc_path,
    normalize_sandbox_relative_path,
    validate_host_path_for_staging,
    validate_sandbox_profile_security,
)


def test_normalize_sandbox_relative_path_safe_paths() -> None:
    """Verify safe relative paths are normalized cleanly."""
    assert normalize_sandbox_relative_path("foo/bar.txt") == "foo/bar.txt"
    assert normalize_sandbox_relative_path("foo\\bar.txt") == "foo/bar.txt"
    assert normalize_sandbox_relative_path("/workspace/src/app.py") == "src/app.py"
    assert normalize_sandbox_relative_path("workspace/tests/test_main.py") == "tests/test_main.py"
    assert normalize_sandbox_relative_path("./local/file.json") == "local/file.json"


def test_normalize_sandbox_relative_path_rejects_traversals() -> None:
    """Verify directory traversals and escapes are strictly rejected."""
    dangerous = [
        "../secret.txt",
        "foo/../../etc/passwd",
        "..\\windows\\system32",
        "%2e%2e/escape.txt",
        "foo/%2e%2e/bar",
        "~/credentials",
        "\x00/nullbyte.txt",
        "",
        "   ",
        "C:\\Windows\\System32",
        "D:/Data/file.txt",
        "\\\\server\\share\\data.txt",
        "//internal/share",
    ]
    for d in dangerous:
        with pytest.raises(SandboxSecurityViolationError):
            normalize_sandbox_relative_path(d)


def test_is_unc_path() -> None:
    """Verify UNC path detection."""
    assert is_unc_path("\\\\server\\share\\file.txt") is True
    assert is_unc_path("//server/share/file.txt") is True
    assert is_unc_path("C:\\normal\\path.txt") is False
    assert is_unc_path("/normal/posix/path") is False


def test_validate_host_path_for_staging_blocked_system_paths() -> None:
    """Verify sensitive host system paths are rejected from staging."""
    blocked = [
        "/etc/shadow",
        "/proc/cpuinfo",
        "/sys/kernel",
        "/var/run/docker.sock",
        "C:/Windows/System32",
    ]
    for b in blocked:
        with pytest.raises(SandboxSecurityViolationError):
            validate_host_path_for_staging(b)


def test_validate_host_path_for_staging_blocked_credentials() -> None:
    """Verify sensitive credential directories in user home are rejected."""
    home = Path.home()
    creds = [
        home / ".ssh" / "id_rsa",
        home / ".aws" / "credentials",
        home / ".docker" / "config.json",
        home / ".env",
    ]
    for c in creds:
        with pytest.raises(SandboxSecurityViolationError):
            validate_host_path_for_staging(c)


def test_validate_host_path_for_staging_allowed_root() -> None:
    """Verify containment within allowed root directory."""
    with tempfile.TemporaryDirectory() as root_dir:
        root = Path(root_dir)
        inside_file = root / "allowed_file.txt"
        inside_file.write_text("safe content", encoding="utf-8")

        # Safe file passes
        valid = validate_host_path_for_staging(inside_file, allowed_root=root)
        assert valid == inside_file.resolve()

        # Outside file fails
        outside_file = root.parent / "outside_file.txt"
        with pytest.raises(SandboxSecurityViolationError):
            validate_host_path_for_staging(outside_file, allowed_root=root)


def test_validate_sandbox_profile_security() -> None:
    """Verify sandbox profile security rule validation."""
    # Hardened profile has no violations
    profile = SandboxProfile(
        profile_id="valid-hardened",
        run_as_user="1000:1000",
        read_only_rootfs=True,
        no_new_privileges=True,
        drop_capabilities=("ALL",),
        seccomp_profile="default",
    )
    violations = validate_sandbox_profile_security(profile)
    assert violations == []

    # Profile with unconfined seccomp produces violation
    bad_seccomp = SandboxProfile(
        profile_id="bad-seccomp",
        run_as_user="1000:1000",
        read_only_rootfs=True,
        no_new_privileges=True,
        seccomp_profile="unconfined",
    )
    violations = validate_sandbox_profile_security(bad_seccomp)
    assert any("unconfined" in v for v in violations)
