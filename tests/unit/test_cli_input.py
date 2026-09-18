"""Unit tests for JARVIS CLI multiline and paste input handling."""

from __future__ import annotations

import io
from unittest.mock import patch

from jarvis.cli import read_user_input


class MockConsole:
    """Mock console that feeds sequential input lines and captures prints."""

    def __init__(self, inputs: list[str]) -> None:
        self.inputs = list(inputs)
        self.prints: list[str] = []

    def input(self, prompt: str = "") -> str:
        if not self.inputs:
            raise EOFError
        return self.inputs.pop(0)

    def print(self, *args: object, **kwargs: object) -> None:
        self.prints.append(" ".join(str(a) for a in args))


def test_read_user_input_single_line() -> None:
    """Verify single line input returns immediately without modification."""
    mock_console = MockConsole(["List files in current directory"])
    with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.readline", return_value=""):
        res = read_user_input(mock_console)  # type: ignore[arg-type]
        assert res == "List files in current directory"


def test_read_user_input_explicit_paste_mode() -> None:
    """Verify /paste command collects multiline input until empty line."""
    mock_console = MockConsole(
        [
            "/paste",
            "def calculate_total(items):",
            "    return sum(item.price for item in items)",
            "",  # Empty line submits
        ]
    )
    res = read_user_input(mock_console)  # type: ignore[arg-type]
    assert res == "def calculate_total(items):\n    return sum(item.price for item in items)"


def test_read_user_input_multiline_slash_end() -> None:
    """Verify /multiline command collects input until /end."""
    mock_console = MockConsole(
        [
            "/multiline",
            "Fix the authentication error in oauth.py",
            "Stacktrace shows expired token",
            "/end",
        ]
    )
    res = read_user_input(mock_console)  # type: ignore[arg-type]
    assert res == "Fix the authentication error in oauth.py\nStacktrace shows expired token"


def test_read_user_input_triple_quotes_double() -> None:
    """Verify triple double-quote block captures multi-line code block."""
    mock_console = MockConsole(
        [
            '"""Write a Python script',
            "that parses markdown files",
            'and counts words"""',
        ]
    )
    res = read_user_input(mock_console)  # type: ignore[arg-type]
    assert res == "Write a Python script\nthat parses markdown files\nand counts words"


def test_read_user_input_triple_quotes_single() -> None:
    """Verify triple single-quote block captures multi-line code block."""
    mock_console = MockConsole(
        [
            "'''SELECT user_id, count(*)",
            "FROM sessions",
            "GROUP BY user_id'''",
        ]
    )
    res = read_user_input(mock_console)  # type: ignore[arg-type]
    assert res == "SELECT user_id, count(*)\nFROM sessions\nGROUP BY user_id"


def test_read_user_input_single_line_triple_quotes() -> None:
    """Verify triple quotes on single line unwraps cleanly."""
    mock_console = MockConsole(['"""Single line in quotes"""'])
    res = read_user_input(mock_console)  # type: ignore[arg-type]
    assert res == "Single line in quotes"


def test_read_user_input_backslash_continuation() -> None:
    """Verify trailing backslash continues input across lines."""
    mock_console = MockConsole(
        [
            "pytest -v \\",
            "--cov=jarvis \\",
            "--cov-report=term",
        ]
    )
    res = read_user_input(mock_console)  # type: ignore[arg-type]
    assert res == "pytest -v\n--cov=jarvis\n--cov-report=term"


def test_read_user_input_piped_non_tty() -> None:
    """Verify non-interactive stream drains all remaining lines."""
    mock_console = MockConsole(["First line from stream"])
    fake_remaining = io.StringIO("Second line from stream\nThird line from stream\n")

    with (
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stdin.read", fake_remaining.read),
    ):
        res = read_user_input(mock_console)  # type: ignore[arg-type]
        assert "First line from stream" in res
        assert "Second line from stream" in res
        assert "Third line from stream" in res
