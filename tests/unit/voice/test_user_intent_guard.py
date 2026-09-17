"""Unit tests for user intent boundary enforcement in Live Tool Bridge.

Verifies that:
1. Read-only user requests strictly prohibit unsolicited mutations.
2. Explicit mutation requests are authorized.
3. Read operations remain permitted during read requests.
4. On-disk byte sizes and content byte sizes are truthfully reported.
"""

from pathlib import Path

import pytest

from jarvis.core.gateway.realtime import LiveToolCall
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import (
    LiveToolBridge,
    is_mutation_authorized_by_intent,
)


def test_is_mutation_authorized_by_intent_pure_read() -> None:
    """Verify read-only prompts return unauthorized for mutating tools."""
    read_only_prompts = [
        "Read requirements.txt and summarize it.",
        "Read that file back and tell me its exact size and summarize its contents.",
        "Examine the logs and tell me what happened.",
        "Check the status of the background task.",
        "Show me the contents of README.md",
        "What are the lines in setup.py?",
        "Inspect the repository structure and list files.",
        "Tell me the size in bytes of the document.",
    ]

    for prompt in read_only_prompts:
        authorized, reason = is_mutation_authorized_by_intent(prompt, "jarvis_write_file")
        assert not authorized, f"Prompt '{prompt}' should have blocked write mutation"
        assert "read-only" in reason

        authorized_del, reason_del = is_mutation_authorized_by_intent(prompt, "jarvis_delete_file")
        assert not authorized_del, f"Prompt '{prompt}' should have blocked delete mutation"
        assert "read-only" in reason_del


def test_is_mutation_authorized_by_intent_explicit_mutation() -> None:
    """Verify explicit mutation prompts return authorized for mutating tools."""
    mutation_prompts = [
        "Create a small file called test.txt containing 'hello'",
        "Write a new section to the file",
        "Append a line to the end of the file",
        "Update the document with new notes",
        "Delete the temporary test file",
        "Remove the file from the workspace",
        "Modify the config setting to enable voice",
        "Replace line 5 with new content",
    ]

    for prompt in mutation_prompts:
        authorized, _ = is_mutation_authorized_by_intent(prompt, "jarvis_write_file")
        assert authorized, f"Prompt '{prompt}' should have authorized mutation"


def test_is_mutation_authorized_by_intent_empty_or_none() -> None:
    """Verify None or empty intent defaults to authorized (governed by Policy Engine)."""
    auth_none, _ = is_mutation_authorized_by_intent(None, "jarvis_write_file")
    assert auth_none

    auth_empty, _ = is_mutation_authorized_by_intent("", "jarvis_write_file")
    assert auth_empty


@pytest.mark.asyncio
async def test_tool_bridge_blocks_unsolicited_write_on_read_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify LiveToolBridge rejects jarvis_write_file when user intent was read-only."""
    monkeypatch.chdir(tmp_path)
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    target_file = tmp_path / "unsolicited_target.txt"
    assert not target_file.exists()

    call = LiveToolCall(
        call_id="call_unsolicited_001",
        name="jarvis_write_file",
        arguments={
            "file_path": "unsolicited_target.txt",
            "content": "unsolicited write content",
        },
    )

    response = await bridge.execute_tool_call(
        call,
        session_id="sess_intent_001",
        user_intent="Read requirements.txt and summarize it.",
    )

    assert response.response.get("unsolicited_mutation") is True
    assert "Security policy blocked action" in response.response.get("error", "")
    assert not target_file.exists(), "Target file must NOT be written when unsolicited"


@pytest.mark.asyncio
async def test_tool_bridge_allows_write_on_explicit_write_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify LiveToolBridge permits jarvis_write_file when user intent is explicit creation."""
    monkeypatch.chdir(tmp_path)
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    target_file = tmp_path / "authorized_target.txt"
    assert not target_file.exists()

    call = LiveToolCall(
        call_id="call_authorized_001",
        name="jarvis_write_file",
        arguments={
            "file_path": "authorized_target.txt",
            "content": "authorized content",
        },
    )

    response = await bridge.execute_tool_call(
        call,
        session_id="sess_intent_002",
        user_intent="Create a small file called authorized_target.txt containing 'authorized content'",
    )

    assert response.response.get("status") == "success"
    assert not response.response.get("unsolicited_mutation")
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "authorized content"


@pytest.mark.asyncio
async def test_tool_bridge_allows_read_on_read_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify LiveToolBridge permits jarvis_read_file on read request and returns truthful byte counts."""
    monkeypatch.chdir(tmp_path)
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    test_file = tmp_path / "sample.txt"
    test_content = "Line one\nLine two\n"
    test_file.write_text(test_content, encoding="utf-8")

    call = LiveToolCall(
        call_id="call_read_001",
        name="jarvis_read_file",
        arguments={"file_path": "sample.txt"},
    )

    response = await bridge.execute_tool_call(
        call,
        session_id="sess_intent_003",
        user_intent="Read that file back and tell me its exact size and summarize its contents.",
    )

    assert response.response.get("status") == "success"
    assert response.response.get("disk_bytes") == test_file.stat().st_size
    assert response.response.get("content_bytes") == len(test_content.encode("utf-8"))
    assert response.response.get("content") == test_content
