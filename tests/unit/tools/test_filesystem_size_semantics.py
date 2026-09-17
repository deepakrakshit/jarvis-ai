"""Unit tests for filesystem size semantics and CRLF/LF normalization."""

import tempfile
from pathlib import Path

from jarvis.tools.native.filesystem import read_file, write_file


def test_filesystem_size_semantics_and_crlf_normalization() -> None:
    """Verify that read_file distinguishes between disk_bytes and content_bytes."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir)
        test_file = ws / "test_semantics.txt"

        # Explicitly write content with Windows CRLF line endings
        raw_crlf_bytes = b"Line 1\r\nLine 2\r\nLine 3\r\n"
        test_file.write_bytes(raw_crlf_bytes)

        actual_disk_size = test_file.stat().st_size
        assert actual_disk_size == len(raw_crlf_bytes)  # 24 bytes on disk

        # Execute read_file
        res = read_file("test_semantics.txt", workspace_root=ws)

        # On Windows, universal newlines decode \r\n to \n in Python strings
        content = res["content"]
        decoded_content_bytes = len(content.encode("utf-8"))

        # Verify contract
        assert res["disk_bytes"] == actual_disk_size
        assert res["size_bytes"] == actual_disk_size  # size_bytes must be actual disk size
        assert res["content_bytes"] == decoded_content_bytes
        assert res["content_bytes"] < res["disk_bytes"]  # 21 bytes vs 24 bytes


def test_write_file_size_reporting() -> None:
    """Verify that write_file returns both disk_bytes and content_bytes."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir)
        res = write_file("out.txt", "Hello\nWorld\n", workspace_root=ws)

        target = ws / "out.txt"
        assert target.exists()
        actual_disk_size = target.stat().st_size

        assert res["disk_bytes"] == actual_disk_size
        assert res["bytes_written"] == actual_disk_size
        assert res["content_bytes"] == len(b"Hello\nWorld\n")
