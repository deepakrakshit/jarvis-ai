"""Unit tests for native tools resilience: delete_file, shell chaining, and runtime path resolution."""

import sys
from pathlib import Path

import pytest

from jarvis.sandbox.runner import LocalProcessSandbox
from jarvis.tools.native import delete_file, dispatch_native_tool, execute_shell, write_file


@pytest.mark.asyncio
async def test_delete_file_success(tmp_path: Path) -> None:
    """Verify delete_file deletes a file within the workspace boundary."""
    target_file = tmp_path / "test_artifact.txt"
    target_file.write_text("temporary content", encoding="utf-8")
    assert target_file.exists()

    result = delete_file(str(target_file), workspace_root=tmp_path)
    assert result["status"] == "deleted"
    assert not target_file.exists()


def test_delete_file_not_found(tmp_path: Path) -> None:
    """Verify delete_file raises FileNotFoundError for non-existent files."""
    with pytest.raises(FileNotFoundError):
        delete_file("non_existent_file.txt", workspace_root=tmp_path)


def test_delete_file_directory_rejected(tmp_path: Path) -> None:
    """Verify delete_file raises IsADirectoryError when targeting a directory."""
    sub_dir = tmp_path / "sub_folder"
    sub_dir.mkdir()
    with pytest.raises(IsADirectoryError):
        delete_file(str(sub_dir), workspace_root=tmp_path)


def test_delete_file_boundary_confinement(tmp_path: Path) -> None:
    """Verify delete_file raises PermissionError for paths outside the workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")

    with pytest.raises(PermissionError):
        delete_file(str(outside_file), workspace_root=workspace)


@pytest.mark.asyncio
async def test_dispatch_native_delete_tool(tmp_path: Path) -> None:
    """Verify dispatch_native_tool routes native:fs:delete_file correctly."""
    f = tmp_path / "dispatch_test.txt"
    write_file(str(f), "content", workspace_root=tmp_path)
    assert f.exists()

    res = await dispatch_native_tool(
        "native:fs:delete_file", {"file_path": str(f), "workspace_root": tmp_path}
    )
    assert res["status"] == "deleted"
    assert not f.exists()


@pytest.mark.asyncio
async def test_execute_shell_chaining(tmp_path: Path) -> None:
    """Verify execute_shell successfully runs chained commands across platforms."""
    cmd = "echo step1 && echo step2"
    res = await execute_shell(cmd, workdir=tmp_path)

    assert res["exit_code"] == 0
    assert "step1" in res["stdout"]
    assert "step2" in res["stdout"]


@pytest.mark.asyncio
async def test_execute_shell_windows_rm_translation(tmp_path: Path) -> None:
    """Verify execute_shell translates rm command on Windows to prevent executable not found."""
    target_file = tmp_path / "to_delete.txt"
    target_file.write_text("delete me", encoding="utf-8")
    assert target_file.exists()

    res = await execute_shell(f"rm -f {target_file.name}", workdir=tmp_path)
    assert res["exit_code"] == 0
    assert not target_file.exists()


def test_runtime_path_resolution(tmp_path: Path) -> None:
    """Verify _resolve_runtime_path prepends existing virtualenv bin/scripts to PATH."""
    fake_venv = tmp_path / ".venv"
    fake_bin = fake_venv / ("Scripts" if sys.platform == "win32" else "bin")
    fake_bin.mkdir(parents=True)

    resolved = LocalProcessSandbox._resolve_runtime_path("original_path", workdir=tmp_path)
    assert str(fake_bin) in resolved
    assert "original_path" in resolved
