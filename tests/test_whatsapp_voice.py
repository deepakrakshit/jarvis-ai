"""Comprehensive Test Suite for JARVIS WhatsApp VoIP Autonomous Calling.

Verifies:
- WhatsAppVoiceCaller initialization, contact resolution, and runner command construction
- Structured call result parsing ([CALL_RESULT_JSON] and JSON fallback)
- Call history retrieval and keyword searching
- WhatsAppNode capability registration and ActionBroker integration
- GeminiLiveBridge tool declaration, capability mapping, and WHATSAPP_NODE dispatch
- ControlPlane WhatsApp calling intent planning
- CLI WhatsApp handler execution
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from jarvis.actions.broker import ActionBroker
from jarvis.actions.registry import capability_registry
from jarvis.cli.commands import (
    handle_whatsapp_call,
    handle_whatsapp_history,
    handle_whatsapp_login,
    handle_whatsapp_logout,
    handle_whatsapp_status,
)
from jarvis.cognition.gemini_live import (
    DEFAULT_LIVE_TOOLS,
    TOOL_TO_CAPABILITY_MAP,
    GeminiLiveBridge,
)
from jarvis.config import settings
from jarvis.contracts.action import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    ExecutionTarget,
    RiskTier,
)
from jarvis.contracts.task import Task, TaskType
from jarvis.core.control_plane import ControlPlane
from jarvis.execution.whatsapp import (
    WhatsAppCallResult,
    WhatsAppNode,
    WhatsAppVoiceCaller,
)
from jarvis.policy.firewall import (
    CAPABILITY_RISK_MAP,
    CAPABILITY_WHATSAPP_CALL,
    CAPABILITY_WHATSAPP_HISTORY,
    CAPABILITY_WHATSAPP_LOGIN,
    CAPABILITY_WHATSAPP_LOGOUT,
    CAPABILITY_WHATSAPP_STATUS,
)


def test_whatsapp_capabilities_firewall_registration() -> None:
    """Verify WhatsApp capabilities are canonically registered in firewall risk map."""
    assert CAPABILITY_WHATSAPP_CALL in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_WHATSAPP_CALL] == RiskTier.MEDIUM

    assert CAPABILITY_WHATSAPP_STATUS in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_WHATSAPP_STATUS] == RiskTier.READ_ONLY

    assert CAPABILITY_WHATSAPP_HISTORY in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_WHATSAPP_HISTORY] == RiskTier.READ_ONLY

    assert CAPABILITY_WHATSAPP_LOGIN in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_WHATSAPP_LOGIN] == RiskTier.LOW

    assert CAPABILITY_WHATSAPP_LOGOUT in CAPABILITY_RISK_MAP
    assert CAPABILITY_RISK_MAP[CAPABILITY_WHATSAPP_LOGOUT] == RiskTier.MEDIUM


def test_whatsapp_voice_caller_initialization() -> None:
    """Verify WhatsAppVoiceCaller resolves directories and commands dynamically."""
    caller = WhatsAppVoiceCaller()
    assert caller._workspace_dir.exists()
    assert caller._extension_dir.name == "whatsapp-voice"
    cmd = caller._resolve_runner_command()
    assert len(cmd) == 3
    assert "tsx" in cmd[1]
    assert "index.ts" in cmd[2]


def test_whatsapp_contact_resolution(tmp_path: Path) -> None:
    """Verify phone number and contact resolution without hardcoding."""
    contacts_file = tmp_path / "contacts.json"
    contacts_file.write_text(
        json.dumps({"alice": "+919876543210", "bob": "9876543211"}),
        encoding="utf-8",
    )

    with patch.object(settings, "WHATSAPP_CONTACTS_FILE", contacts_file):
        with patch.object(settings, "WHATSAPP_DEFAULT_COUNTRY_CODE", "91"):
            caller = WhatsAppVoiceCaller()

            # Known contact name
            res_alice = caller.resolve_contact("Alice")
            assert res_alice["phone_number"] == "919876543210"
            assert res_alice["formatted"] == "+919876543210"

            # 10-digit number with default country code prepended
            res_number = caller.resolve_contact("9876500000")
            assert res_number["phone_number"] == "919876500000"
            assert res_number["formatted"] == "+919876500000"

            # Direct international format
            res_intl = caller.resolve_contact("+14155552671")
            assert res_intl["phone_number"] == "14155552671"
            assert res_intl["formatted"] == "+14155552671"


def test_whatsapp_result_json_parsing() -> None:
    """Verify structured parsing of CLI output containing [CALL_RESULT_JSON] markers."""
    caller = WhatsAppVoiceCaller()

    raw_stdout = (
        "Booting WhatsApp VoIP Engine...\n"
        "Connecting WebRTC...\n"
        "[CALL_RESULT_JSON]\n"
        '{"success": true, "callId": "CALL-999", "targetNumber": "+919876543210", '
        '"durationMs": 45000, "error": null}\n'
        "[/CALL_RESULT_JSON]\n"
        "Runner finished cleanly.\n"
    )

    result = caller._parse_json_result(raw_stdout)
    assert result is not None
    assert result.success is True
    assert result.call_id == "CALL-999"
    assert result.target_number == "+919876543210"
    assert result.duration_seconds == 45.0
    assert result.error is None


def test_whatsapp_result_json_fallback_parsing() -> None:
    """Verify fallback parsing of standalone JSON output lines."""
    caller = WhatsAppVoiceCaller()

    raw_stdout = (
        "Connecting...\n"
        '{"success": false, "callId": "CALL-ERR", "error": "Recipient did not answer"}\n'
    )

    result = caller._parse_json_result(raw_stdout)
    assert result is not None
    assert result.success is False
    assert result.call_id == "CALL-ERR"
    assert result.error == "Recipient did not answer"


def test_whatsapp_call_history_and_search(tmp_path: Path) -> None:
    """Verify reading and filtering call history from logs."""
    history_file = tmp_path / "call-history.json"
    history_file.write_text(
        json.dumps(
            [
                {
                    "callId": "CALL-1",
                    "target": "John",
                    "objective": "Meeting reminder",
                    "summary": "John confirmed he will attend the 3 PM sync.",
                    "recipientReply": "Yes, I will be there.",
                    "status": "COMPLETED",
                },
                {
                    "callId": "CALL-2",
                    "target": "Sarah",
                    "objective": "Server outage notice",
                    "summary": "Informed Sarah about database restart.",
                    "recipientReply": "Acknowledged.",
                    "status": "COMPLETED",
                },
            ]
        ),
        encoding="utf-8",
    )

    caller = WhatsAppVoiceCaller()
    with patch.object(caller, "get_call_history") as mock_hist:
        mock_hist.return_value = json.loads(history_file.read_text(encoding="utf-8"))
        results = caller.search_call_history("meeting")
        assert len(results) == 1
        assert results[0]["callId"] == "CALL-1"

        sarah_results = caller.search_call_history("sarah")
        assert len(sarah_results) == 1
        assert sarah_results[0]["callId"] == "CALL-2"


@pytest.mark.asyncio
async def test_whatsapp_caller_place_call_mock() -> None:
    """Verify place_call invokes subprocess and returns structured result."""
    caller = WhatsAppVoiceCaller()

    mock_process = AsyncMock()
    mock_process.returncode = 0
    fake_payload = (
        "[CALL_RESULT_JSON]\n"
        '{"success": true, "callId": "CALL-ABC", "targetNumber": "+919876543210", '
        '"durationMs": 12500}\n'
        "[/CALL_RESULT_JSON]\n"
    )
    mock_process.communicate = AsyncMock(return_value=(fake_payload.encode("utf-8"), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        with patch.object(
            WhatsAppVoiceCaller, "is_available", new_callable=PropertyMock, return_value=True
        ):
            res = await caller.place_call(
                target="John",
                objective="Remind about dinner tonight",
                conversation_mode="MESSAGE_DELIVERY",
            )
            assert mock_exec.called
            assert res.success is True
            assert res.call_id == "CALL-ABC"
            assert res.target_number == "+919876543210"
            assert res.duration_seconds == 12.5


@pytest.mark.asyncio
async def test_whatsapp_node_capabilities_registration() -> None:
    """Verify WhatsAppNode registers all capabilities with capability_registry."""
    mock_caller = MagicMock(spec=WhatsAppVoiceCaller)
    mock_caller.is_available = True
    mock_caller.has_persisted_session = True
    mock_caller._auth_dir = Path("auth")
    mock_caller._extension_dir = Path("ext")
    mock_caller.place_call = AsyncMock(
        return_value=WhatsAppCallResult(
            success=True,
            call_id="CALL-TEST-NODE",
            target_number="+919876543210",
            duration_seconds=30.0,
            summary_text="Delivered message.",
            recipient_reply="Got it, thanks.",
        )
    )
    mock_caller.get_call_history.return_value = [{"callId": "C1"}]
    mock_caller.search_call_history.return_value = [{"callId": "C1"}]

    node = WhatsAppNode(caller=mock_caller)
    node.register_capabilities()

    assert capability_registry.is_registered(CAPABILITY_WHATSAPP_CALL)
    assert capability_registry.is_registered(CAPABILITY_WHATSAPP_STATUS)
    assert capability_registry.is_registered(CAPABILITY_WHATSAPP_HISTORY)

    # Test call handler dispatch
    call_handler = capability_registry.get_handler(CAPABILITY_WHATSAPP_CALL)
    assert call_handler is not None
    req = ActionRequest(
        task_id="T1",
        session_id="S1",
        capability=CAPABILITY_WHATSAPP_CALL,
        arguments={"target": "Alice", "objective": "Test"},
        target=ExecutionTarget.WHATSAPP_NODE,
    )
    res_dict = await call_handler(req)
    assert res_dict["success"] is True
    assert res_dict["call_id"] == "CALL-TEST-NODE"
    assert res_dict["summary"] == "Delivered message."

    # Test status handler dispatch
    status_handler = capability_registry.get_handler(CAPABILITY_WHATSAPP_STATUS)
    assert status_handler is not None
    stat_res = await status_handler(req)
    assert stat_res["available"] is True
    assert stat_res["authenticated"] is True

    # Test history handler dispatch
    hist_handler = capability_registry.get_handler(CAPABILITY_WHATSAPP_HISTORY)
    assert hist_handler is not None
    hist_res = await hist_handler(req)
    assert hist_res["count"] == 1


def test_gemini_live_tools_include_whatsapp() -> None:
    """Verify Gemini Live function declarations include WhatsApp tools."""
    tool_names = [t["name"] for t in DEFAULT_LIVE_TOOLS]
    assert "whatsapp_call" in tool_names
    assert "whatsapp_get_latest_call" in tool_names
    assert "whatsapp_status" in tool_names

    assert TOOL_TO_CAPABILITY_MAP["whatsapp_call"] == CAPABILITY_WHATSAPP_CALL
    assert TOOL_TO_CAPABILITY_MAP["whatsapp_get_latest_call"] == CAPABILITY_WHATSAPP_HISTORY
    assert TOOL_TO_CAPABILITY_MAP["whatsapp_status"] == CAPABILITY_WHATSAPP_STATUS


@pytest.mark.asyncio
async def test_gemini_live_routes_to_whatsapp_node() -> None:
    """Verify GeminiLiveBridge routes whatsapp_call tool calls to ExecutionTarget.WHATSAPP_NODE."""
    mock_broker = AsyncMock()
    mock_broker.execute = AsyncMock(
        return_value=ActionResult(
            action_id="ACT-WA-1",
            task_id="LIVE-call_123",
            status=ActionStatus.SUCCEEDED,
            execution_target=ExecutionTarget.WHATSAPP_NODE,
            output={"success": True, "call_id": "CALL-123", "target_number": "+919876543210"},
            verified=True,
            audit_logged=True,
        )
    )

    bridge = GeminiLiveBridge(broker=mock_broker)
    mock_session = AsyncMock()
    bridge._active_session = mock_session

    fake_tool_call = MagicMock()
    fake_func_call = MagicMock()
    fake_func_call.id = "call_wa_123"
    fake_func_call.name = "whatsapp_call"
    fake_func_call.args = {
        "target": "John",
        "objective": "Ask if he is joining the dinner",
    }
    fake_tool_call.function_calls = [fake_func_call]

    await bridge._handle_tool_call(fake_tool_call)

    # Verify ActionBroker execution target
    assert mock_broker.execute.called
    req = mock_broker.execute.call_args[0][0]
    assert req.capability == CAPABILITY_WHATSAPP_CALL
    assert req.target == ExecutionTarget.WHATSAPP_NODE
    assert req.arguments["target"] == "John"

    # Verify tool response dispatch
    assert mock_session.send_tool_response.called
    responses = mock_session.send_tool_response.call_args[1]["function_responses"]
    assert len(responses) == 1
    assert responses[0].id == "call_wa_123"
    assert responses[0].response["status"] == "success"
    assert responses[0].response["output"]["call_id"] == "CALL-123"


@pytest.mark.asyncio
async def test_control_plane_plans_whatsapp_call() -> None:
    """Verify ControlPlane plans a CAPABILITY_WHATSAPP_CALL for WhatsApp calling intents."""
    mock_broker = AsyncMock(spec=ActionBroker)
    cp = ControlPlane(broker=mock_broker)

    task = Task(
        task_id="TASK-WA-TEST",
        session_id="SESS-1",
        raw_intent="call John on whatsapp to ask if he has the documents",
        task_type=TaskType.WINDOWS_CONTROL,
    )

    state = await cp._node_plan({"task": task, "current_action_idx": 0})
    plan = state.get("plan", [])

    assert len(plan) == 1
    assert plan[0].capability == CAPABILITY_WHATSAPP_CALL
    assert plan[0].target == ExecutionTarget.WHATSAPP_NODE
    assert plan[0].risk_tier == RiskTier.MEDIUM
    assert plan[0].arguments["target"] == "John"
    assert "documents" in plan[0].arguments["objective"]


@pytest.mark.asyncio
async def test_cli_whatsapp_handlers_mock() -> None:
    """Verify CLI WhatsApp handlers for status, call, and history."""
    mock_caller = MagicMock(spec=WhatsAppVoiceCaller)
    mock_caller.is_available = True
    mock_caller.has_persisted_session = True
    mock_caller._auth_dir = Path("auth")
    mock_caller._extension_dir = Path("ext")
    mock_caller.place_call = AsyncMock(
        return_value=WhatsAppCallResult(
            success=True,
            call_id="CALL-CLI-1",
            target_number="+919876543210",
            duration_seconds=15.0,
            summary_text="Meeting confirmed.",
            recipient_reply="I'll be there.",
        )
    )
    mock_caller.get_call_history.return_value = [
        {"callId": "CALL-CLI-1", "target": "John", "status": "COMPLETED"}
    ]
    mock_caller.search_call_history.return_value = [
        {"callId": "CALL-CLI-1", "target": "John", "status": "COMPLETED"}
    ]

    with patch("jarvis.cli.commands.get_whatsapp_caller", return_value=mock_caller):
        # 1. status
        stat = handle_whatsapp_status()
        assert stat["available"] is True
        assert stat["authenticated"] is True

        # 2. call
        call_res = await handle_whatsapp_call(
            target="John",
            objective="Confirm sync",
        )
        assert call_res["success"] is True
        assert call_res["call_id"] == "CALL-CLI-1"
        assert call_res["summary"] == "Meeting confirmed."

        # 3. history
        hist = handle_whatsapp_history(limit=5)
        assert len(hist) == 1
        assert hist[0]["callId"] == "CALL-CLI-1"

        # 4. login
        mock_caller.login = AsyncMock(
            return_value={
                "success": True,
                "authenticated": True,
                "message": "Device linked.",
            }
        )
        login_res = await handle_whatsapp_login(force_refresh=True)
        assert login_res["success"] is True
        assert login_res["authenticated"] is True
        mock_caller.login.assert_awaited_once_with(force_refresh=True, wait_for_scan=True)

        # 5. logout
        mock_caller.logout.return_value = {
            "success": True,
            "purged_files": 3,
            "message": "Logged out.",
        }
        logout_res = handle_whatsapp_logout()
        assert logout_res["success"] is True
        assert logout_res["purged_files"] == 3


@pytest.mark.asyncio
async def test_whatsapp_caller_login_success(tmp_path: Path) -> None:
    """Verify WhatsAppVoiceCaller.login runs runner command and parses JSON payload."""
    caller = WhatsAppVoiceCaller(extension_dir=tmp_path, auth_dir=tmp_path / "auth")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout.readline = AsyncMock(
        side_effect=[
            b'[CALL_RESULT_JSON]{"success": true, "authenticated": true, "message": "Device linked."}[/CALL_RESULT_JSON]\n',
            b"",
        ]
    )
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        res = await caller.login(force_refresh=False, wait_for_scan=True)
        assert res["success"] is True
        assert res["authenticated"] is True
        assert res["message"] == "Device linked."


@pytest.mark.asyncio
async def test_whatsapp_caller_login_qr_ready_instant_return(tmp_path: Path) -> None:
    """Verify WhatsAppVoiceCaller.login returns immediately when QR is ready in non-blocking mode."""
    caller = WhatsAppVoiceCaller(extension_dir=tmp_path, auth_dir=tmp_path / "auth")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    html_file = tmp_path / "login_qr.html"
    html_file.write_text("<html></html>", encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdout.readline = AsyncMock(
        side_effect=[
            f'[WHATSAPP_QR_JSON]{{"qr": "mock-qr-code", "htmlPath": "{html_file.as_posix()}"}}[/WHATSAPP_QR_JSON]\n'.encode(
                "utf-8"
            ),
            b"",
        ]
    )

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        res = await caller.login(force_refresh=False, wait_for_scan=False)
        assert res["success"] is True
        assert res["status"] == "qr_ready"
        assert html_file.as_posix() in res["html_path"]


@pytest.mark.asyncio
async def test_whatsapp_caller_login_timeout(tmp_path: Path) -> None:
    """Verify WhatsAppVoiceCaller.login handles timeout gracefully."""
    caller = WhatsAppVoiceCaller(extension_dir=tmp_path, auth_dir=tmp_path / "auth")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_proc.stderr.read = AsyncMock(return_value=b"")
    mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        res = await caller.login(wait_for_scan=True, timeout_seconds=1)
        assert res["success"] is False
        assert "timed out" in res["error"]
        mock_proc.kill.assert_called_once()


def test_whatsapp_caller_logout(tmp_path: Path) -> None:
    """Verify WhatsAppVoiceCaller.logout cleans authentication files."""
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "creds.json").write_text("{}", encoding="utf-8")
    (auth_dir / "app-state-sync-version.json").write_text("{}", encoding="utf-8")

    sub_dir = auth_dir / "keys"
    sub_dir.mkdir()
    (sub_dir / "pre-key-1.json").write_text("{}", encoding="utf-8")

    caller = WhatsAppVoiceCaller(extension_dir=tmp_path, auth_dir=auth_dir)
    assert caller.has_persisted_session is True

    result = caller.logout()
    assert result["success"] is True
    assert result["purged_files"] >= 2
    assert caller.has_persisted_session is False


@pytest.mark.asyncio
async def test_whatsapp_caller_warmup_and_cleanup_before_call(tmp_path: Path) -> None:
    """Verify silent warmup spawns background process and is terminated prior to outbound calls."""
    caller = WhatsAppVoiceCaller(extension_dir=tmp_path, auth_dir=tmp_path / "auth")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "auth").mkdir(parents=True, exist_ok=True)
    (tmp_path / "auth" / "creds.json").write_text("{}", encoding="utf-8")

    mock_warmup_proc = MagicMock()
    mock_warmup_proc.returncode = None
    mock_warmup_proc.terminate = MagicMock()

    mock_call_proc = MagicMock()
    mock_call_proc.returncode = 0
    mock_call_proc.communicate = AsyncMock(
        return_value=(
            b'[CALL_RESULT_JSON]{"success": true, "callId": "CALL-W1"}[/CALL_RESULT_JSON]',
            b"",
        )
    )

    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(side_effect=[mock_warmup_proc, mock_call_proc]),
    ):
        await caller.warmup()
        assert caller._warmup_process == mock_warmup_proc

        # Placing call should terminate alive warmup process to release socket
        call_res = await caller.place_call(target="Alice", objective="Hello")
        assert call_res.success is True
        mock_warmup_proc.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_whatsapp_node_login_and_logout_handlers() -> None:
    """Verify WhatsAppNode registers and dispatches login and logout actions."""
    mock_caller = MagicMock(spec=WhatsAppVoiceCaller)
    mock_caller.login = AsyncMock(
        return_value={"success": True, "authenticated": True, "message": "Linked"}
    )
    mock_caller.logout.return_value = {"success": True, "message": "Purged"}

    node = WhatsAppNode(caller=mock_caller)
    node.register_capabilities()

    broker = ActionBroker()

    # Test login capability
    login_req = ActionRequest(
        task_id="TEST-LOGIN",
        session_id="TEST-SESSION",
        capability=CAPABILITY_WHATSAPP_LOGIN,
        arguments={"force_refresh": True},
        target=ExecutionTarget.WHATSAPP_NODE,
    )
    login_result = await broker.execute(login_req)
    assert login_result.status == ActionStatus.SUCCEEDED
    assert login_result.output["authenticated"] is True
    mock_caller.login.assert_awaited_once_with(force_refresh=True, wait_for_scan=False)

    # Test logout capability
    logout_req = ActionRequest(
        task_id="TEST-LOGOUT",
        session_id="TEST-SESSION",
        capability=CAPABILITY_WHATSAPP_LOGOUT,
        arguments={},
        target=ExecutionTarget.WHATSAPP_NODE,
    )
    logout_result = await broker.execute(logout_req)
    assert logout_result.status == ActionStatus.SUCCEEDED
    assert logout_result.output["message"] == "Purged"
    mock_caller.logout.assert_called_once()


@pytest.mark.asyncio
async def test_control_plane_whatsapp_login_and_logout_planning() -> None:
    """Verify ControlPlane generates structured plans for WhatsApp login and logout intents."""
    cp = ControlPlane()

    # 1. Login/QR intent
    task_login = Task(
        session_id="SESS-WA-LOGIN",
        raw_intent="give me the qr to login on whatsapp",
        task_type=TaskType.CONVERSATION,
    )
    res_login = await cp._node_plan({"task": task_login})
    plan_login = res_login.get("plan", [])
    assert len(plan_login) == 1
    assert plan_login[0].capability == CAPABILITY_WHATSAPP_LOGIN
    assert plan_login[0].target == ExecutionTarget.WHATSAPP_NODE
    assert plan_login[0].risk_tier == RiskTier.LOW

    # 2. Logout intent
    task_logout = Task(
        session_id="SESS-WA-LOGOUT",
        raw_intent="log out and disconnect from whatsapp",
        task_type=TaskType.CONVERSATION,
    )
    res_logout = await cp._node_plan({"task": task_logout})
    plan_logout = res_logout.get("plan", [])
    assert len(plan_logout) == 1
    assert plan_logout[0].capability == CAPABILITY_WHATSAPP_LOGOUT
    assert plan_logout[0].target == ExecutionTarget.WHATSAPP_NODE
    assert plan_logout[0].risk_tier == RiskTier.MEDIUM


def test_gemini_live_bridge_tools_include_whatsapp_login_and_logout() -> None:
    """Verify GeminiLiveBridge tool definitions declare login and logout."""
    tool_names = [t["name"] for t in DEFAULT_LIVE_TOOLS]
    assert "whatsapp_login" in tool_names
    assert "whatsapp_logout" in tool_names
    assert TOOL_TO_CAPABILITY_MAP["whatsapp_login"] == CAPABILITY_WHATSAPP_LOGIN
    assert TOOL_TO_CAPABILITY_MAP["whatsapp_logout"] == CAPABILITY_WHATSAPP_LOGOUT


def test_whatsapp_call_result_summary_and_reply_extraction(tmp_path: Path) -> None:
    """Verify _build_call_result extracts summary and recipient reply directly and with fallback."""
    caller = WhatsAppVoiceCaller()

    # Case 1: Direct in data
    res1 = caller._build_call_result(
        {
            "success": True,
            "callId": "C101",
            "targetNumber": "+917088669912",
            "durationMs": 29000,
            "summary": "Recipient confirmed attendance.",
            "recipientReply": "Yes, I will come.",
        }
    )
    assert res1.summary_text == "Recipient confirmed attendance."
    assert res1.recipient_reply == "Yes, I will come."
    assert res1.duration_seconds == 29.0

    # Case 2: From summary_path JSON with raw transcript fallback
    transcript_file = tmp_path / "transcript-C102.json"
    transcript_file.write_text(
        json.dumps(
            {
                "callId": "C102",
                "metrics": {"call": {"targetNumber": "917088669912", "durationMs": 25000}},
                "transcript": [
                    {"role": "assistant", "text": "Are you coming to college tomorrow?"},
                    {"role": "user", "text": "Yes."},
                    {"role": "assistant", "text": "Thank you, goodbye!"},
                ],
            }
        ),
        encoding="utf-8",
    )

    res2 = caller._build_call_result(
        {
            "success": True,
            "callId": "C102",
            "targetNumber": "+917088669912",
            "durationMs": 25000,
            "summaryPath": str(transcript_file),
        }
    )
    assert res2.recipient_reply == "Yes."
    assert res2.summary_text is not None
    assert "Yes." in res2.summary_text


def test_whatsapp_history_transcript_fallback(tmp_path: Path) -> None:
    """Verify get_call_history discovers and extracts past calls from transcript files if history JSON missing."""
    logs_dir = tmp_path / "data" / "whatsapp" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    tf = logs_dir / "transcript-TEST1-2026-09-24T00-00-00Z.json"
    tf.write_text(
        json.dumps(
            {
                "callId": "TEST1",
                "timestamp": "2026-09-24T00:00:00Z",
                "metrics": {
                    "call": {
                        "targetNumber": "917088669912",
                        "durationMs": 29059,
                        "endReason": "ended",
                    }
                },
                "transcript": [
                    {"role": "assistant", "text": "Hello!"},
                    {"role": "user", "text": "Yes."},
                ],
            }
        ),
        encoding="utf-8",
    )

    caller = WhatsAppVoiceCaller()
    caller._workspace_dir = tmp_path
    caller._extension_dir = tmp_path

    history = caller.get_call_history(limit=5)
    assert len(history) >= 1
    assert history[0]["callId"] == "TEST1"
    assert history[0]["recipientReply"] == "Yes."
    assert "Yes." in history[0]["summary"]
