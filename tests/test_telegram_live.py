"""Unit tests for dedicated Telegram Gemini 3.8 Live session subsystem.

Tests tool declaration schema, tool execution routing, live turn processing,
artifact delivery, session manager isolation, and service delegation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message, User

from jarvis.contracts.action import ActionResult, ActionStatus, ExecutionTarget
from jarvis.telegram.live_session import (
    TELEGRAM_LIVE_TOOLS_SPEC,
    TOOL_TO_CAPABILITY_MAP,
    TelegramLiveSession,
    TelegramLiveSessionManager,
    TelegramLiveTurnResult,
)
from jarvis.telegram.service import TelegramService


def test_telegram_live_tools_declarations() -> None:
    """Verify tool specifications are well-formed and contain canonical capabilities."""
    assert len(TELEGRAM_LIVE_TOOLS_SPEC) >= 20
    names = {tool["name"] for tool in TELEGRAM_LIVE_TOOLS_SPEC}

    assert "filesystem_search" in names
    assert "filesystem_read" in names
    assert "filesystem_list" in names
    assert "system_screenshot" in names
    assert "whatsapp_call" in names
    assert "browser_navigate" in names
    assert "shell_execute" in names
    assert "web_search" in names
    assert "memory_store" in names
    assert "memory_search" in names

    for tool in TELEGRAM_LIVE_TOOLS_SPEC:
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool


def test_telegram_live_capability_mapping() -> None:
    """Verify canonical mapping exists from tool names to firewall capability IDs."""
    assert TOOL_TO_CAPABILITY_MAP["filesystem_search"] == "filesystem.search"
    assert TOOL_TO_CAPABILITY_MAP["filesystem_read"] == "filesystem.read"
    assert TOOL_TO_CAPABILITY_MAP["system_screenshot"] == "computer.screenshot"
    assert TOOL_TO_CAPABILITY_MAP["whatsapp_call"] == "whatsapp.call"
    assert TOOL_TO_CAPABILITY_MAP["browser_navigate"] == "browser.navigate"


@pytest.mark.asyncio
async def test_telegram_live_execute_memory_store_and_search() -> None:
    """Verify memory plane tools execute correctly through Telegram Live session."""
    session = TelegramLiveSession(chat_id=12345, api_key="dummy_api_key")

    # Store memory
    res, art = await session._execute_tool(
        func_name="memory_store",
        args={"content": "Operator prefers dark mode and concise summaries.", "key": "pref-ui"},
        call_id="call-01",
    )
    assert res["status"] == "success"
    assert res["stored"] is True
    assert art is None

    # Search memory
    res_search, art_search = await session._execute_tool(
        func_name="memory_search",
        args={"query": "dark mode"},
        call_id="call-02",
    )
    assert res_search["status"] == "success"
    assert res_search["count"] >= 1
    assert art_search is None


@pytest.mark.asyncio
async def test_telegram_live_execute_action_broker_with_artifact(tmp_path: Path) -> None:
    """Verify tool execution via ActionBroker catches artifact file paths."""
    mock_broker = MagicMock()
    fake_screenshot = tmp_path / "screenshot_test.png"
    fake_screenshot.write_bytes(b"\x89PNG\r\n\x1a\nfake_image_data")

    mock_broker.execute = AsyncMock(
        return_value=ActionResult(
            action_id="ACT-MOCK-1",
            task_id="TG-LIVE-call-03",
            status=ActionStatus.SUCCEEDED,
            execution_target=ExecutionTarget.WINDOWS_NODE,
            output={"artifact_path": str(fake_screenshot), "width": 1920, "height": 1080},
            verified=True,
        )
    )

    session = TelegramLiveSession(chat_id=12345, api_key="dummy_api_key", broker=mock_broker)

    res, art = await session._execute_tool(
        func_name="system_screenshot",
        args={},
        call_id="call-03",
    )

    assert res["status"] == "success"
    assert res["action_status"] == "SUCCEEDED"
    assert art is not None
    assert art.name == "screenshot_test.png"


@pytest.mark.asyncio
async def test_telegram_live_process_user_turn_mocked_gemini() -> None:
    """Verify end-to-end turn processing with simulated Gemini 3.8 Live session."""
    session = TelegramLiveSession(chat_id=999, api_key="dummy_api_key")

    mock_active_session = AsyncMock()

    # Create mock response chunks
    mock_tc_chunk = MagicMock()
    mock_tc_chunk.session_resumption_update = None
    mock_tc_chunk.tool_call = MagicMock()
    mock_call = MagicMock()
    mock_call.id = "call_fs_01"
    mock_call.name = "filesystem_search"
    mock_call.args = {"query": "java pbl", "extension": "ppt"}
    mock_tc_chunk.tool_call.function_calls = [mock_call]
    mock_tc_chunk.server_content = None

    mock_tc_complete = MagicMock()
    mock_tc_complete.session_resumption_update = None
    mock_tc_complete.tool_call = None
    mock_tc_complete.server_content = MagicMock()
    mock_tc_complete.server_content.output_transcription = None
    mock_tc_complete.server_content.model_turn = None
    mock_tc_complete.server_content.turn_complete = True

    mock_text_chunk = MagicMock()
    mock_text_chunk.session_resumption_update = MagicMock(new_handle="handle_resumption_abc")
    mock_text_chunk.tool_call = None
    mock_text_chunk.server_content = MagicMock()
    mock_text_chunk.server_content.output_transcription = MagicMock(
        text="I found your Java PBL presentation in Downloads."
    )
    mock_text_chunk.server_content.model_turn = None
    mock_text_chunk.server_content.turn_complete = True

    # Simulate chunks delivered across sequential receive calls
    turn_counter = 0

    def mock_receive_factory() -> AsyncIterator[Any]:
        async def mock_receive_stream() -> AsyncIterator[Any]:
            nonlocal turn_counter
            turn_counter += 1
            if turn_counter == 1:
                yield mock_tc_chunk
                yield mock_tc_complete
            else:
                yield mock_text_chunk

        return mock_receive_stream()

    mock_active_session.receive = mock_receive_factory

    # Mock tool execution
    session._execute_tool = AsyncMock(  # type: ignore[method-assign]
        return_value=({"found": True, "matches": [{"name": "Java_PBL.pptx"}]}, None)
    )

    session._active_session = mock_active_session
    session._connected = True

    result = await session.process_user_turn(user_text="Find my java pbl presentation in downloads")

    assert result.error is None
    assert "Java PBL" in result.text
    assert "filesystem_search" in result.tools_called
    mock_active_session.send_client_content.assert_called_once()
    mock_active_session.send_tool_response.assert_called_once()
    assert session._resumption_handle == "handle_resumption_abc"


@pytest.mark.asyncio
async def test_telegram_live_session_manager_isolation() -> None:
    """Verify TelegramLiveSessionManager isolates sessions per chat_id."""
    manager = TelegramLiveSessionManager()
    session_1 = manager.get_or_create_session(chat_id=101)
    session_2 = manager.get_or_create_session(chat_id=202)

    assert session_1 is not session_2
    assert session_1.chat_id == 101
    assert session_2.chat_id == 202

    # Closing all cleans up sessions
    await manager.close_all()
    assert len(manager._sessions) == 0


@pytest.mark.asyncio
async def test_telegram_service_delegates_to_live_manager(tmp_path: Path) -> None:
    """Verify TelegramService.on_text_message routes to TelegramLiveSessionManager."""
    mock_live_mgr = MagicMock(spec=TelegramLiveSessionManager)
    fake_screenshot = tmp_path / "desktop.png"
    fake_screenshot.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    mock_live_mgr.process_message = AsyncMock(
        return_value=TelegramLiveTurnResult(
            text="Here is your desktop screenshot, Sir.",
            artifacts=[fake_screenshot],
            tools_called=["system_screenshot"],
        )
    )

    mock_authorizer = AsyncMock()
    mock_authorizer.is_authorized = AsyncMock(return_value=True)

    mock_db = MagicMock()
    mock_db.get_or_create_telegram_chat_session = MagicMock(return_value="SESS-TG-1")

    service = TelegramService(
        bot_token="dummy_token_123",
        database=mock_db,
        authorizer=mock_authorizer,
        live_mgr=mock_live_mgr,
    )

    from aiogram import Dispatcher

    dp = Dispatcher()
    service._register_handlers(dp)

    mock_message = MagicMock(spec=Message)
    mock_message.text = "Show me my screen"
    mock_message.chat = MagicMock(id=555)
    mock_message.from_user = User(id=555, is_bot=False, first_name="Deepak")
    mock_message.answer = AsyncMock()
    mock_message.answer_photo = AsyncMock()

    with patch("jarvis.config.settings.TELEGRAM_GEMINI_API_KEY", "dummy_key"):
        handlers = dp.message.handlers
        for h in handlers:
            if "on_text_message" in getattr(h.callback, "__name__", ""):
                await h.callback(mock_message)
                break

    mock_live_mgr.process_message.assert_called_once_with(
        chat_id=555, user_text="Show me my screen"
    )
    mock_message.answer_photo.assert_called_once()
    mock_message.answer.assert_called_once_with("Here is your desktop screenshot, Sir.")
