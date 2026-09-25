"""Autonomous WhatsApp VoIP Calling Engine for JARVIS.

Bridges the Python cognitive kernel with the high-performance
Node.js WebAssembly / WebRTC WhatsApp VoIP runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from pydantic import BaseModel, Field

from jarvis.config import settings

logger = logging.getLogger(__name__)


class WhatsAppCallResult(BaseModel):
    """Result of an autonomous WhatsApp voice call."""

    success: bool
    call_id: str = Field(default="")
    target_number: str = Field(default="")
    duration_seconds: float = Field(default=0.0)
    transcript_path: Optional[str] = Field(default=None)
    summary_path: Optional[str] = Field(default=None)
    summary_text: Optional[str] = Field(default=None)
    recipient_reply: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)


class WhatsAppVoiceCaller:
    """Manages autonomous WhatsApp VoIP calls via the native execution extension."""

    def __init__(
        self,
        extension_dir: Optional[Path] = None,
        auth_dir: Optional[Path] = None,
    ) -> None:
        self._workspace_dir = settings.WORKSPACE_DIR
        if extension_dir:
            self._extension_dir = extension_dir
        else:
            default_ext = self._workspace_dir / "substrate" / "extensions" / "whatsapp"
            fallback_ext = self._workspace_dir / "substrate" / "extensions" / "whatsapp-voice"
            self._extension_dir = default_ext if default_ext.exists() else fallback_ext
        self._auth_dir = auth_dir or settings.WHATSAPP_AUTH_DIR or self._extension_dir / "auth"
        self._warmup_process: Optional[asyncio.subprocess.Process] = None

    @property
    def is_available(self) -> bool:
        """Check whether the WhatsApp voice extension is present."""
        return (self._extension_dir / "package.json").exists()

    @property
    def has_persisted_session(self) -> bool:
        """Check whether authenticated WhatsApp credentials exist."""
        if not self._auth_dir.exists():
            return False
        try:
            return any(
                f.name.startswith("creds.json") or f.name.endswith(".json")
                for f in self._auth_dir.iterdir()
                if f.is_file()
            )
        except Exception:
            return False

    def _resolve_runner_command(self) -> List[str]:
        """Resolve executable command for running the TypeScript VoIP engine."""
        npx_bin = shutil.which("npx")
        if not npx_bin:
            npx_bin = "npx.cmd" if os.name == "nt" else "npx"

        return [
            npx_bin,
            "tsx",
            str(self._extension_dir / "src" / "index.ts"),
        ]

    async def place_call(
        self,
        target: str,
        objective: str,
        conversation_mode: Optional[str] = None,
        duration_ms: Optional[int] = None,
        on_status: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
    ) -> WhatsAppCallResult:
        """Initiate an autonomous full-duplex WhatsApp VoIP call to a target."""
        if not self.is_available:
            return WhatsAppCallResult(
                success=False,
                error=f"WhatsApp voice extension not found at {self._extension_dir}",
            )

        # Release background warmup or login socket connection before placing an outbound call
        for proc in (self._warmup_process, getattr(self, "_login_process", None)):
            if proc is not None and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.sleep(0.2)
                except Exception:
                    pass

        cmd = self._resolve_runner_command()
        effective_mode = conversation_mode or settings.WHATSAPP_CONVERSATION_MODE
        effective_duration = duration_ms or settings.WHATSAPP_CALL_TIMEOUT_MS

        args = [
            *cmd,
            "--target",
            target,
            "--objective",
            objective,
            "--mode",
            effective_mode,
            "--duration",
            str(effective_duration),
            "--json",
        ]

        env = os.environ.copy()
        if settings.GEMINI_API_KEY:
            env["GEMINI_API_KEY"] = settings.GEMINI_API_KEY
        env["GEMINI_MODEL"] = settings.MODEL_MAP_GEMINI_LIVE
        env["WHATSAPP_AUTH_DIR"] = str(self._auth_dir)
        env["WHATSAPP_DB_PATH"] = str(settings.WHATSAPP_DB_PATH)
        env["WORKSPACE_DIR"] = str(self._workspace_dir)
        env["USER_NAME"] = settings.USER_CALLSIGN
        env["USER_CALLSIGN"] = settings.USER_CALLSIGN
        env["APP_NAME"] = settings.APP_NAME
        env["WHATSAPP_CALL_LANGUAGE"] = settings.WHATSAPP_CALL_LANGUAGE
        env["DEFAULT_COUNTRY_CODE"] = settings.WHATSAPP_DEFAULT_COUNTRY_CODE

        logger.info(
            "Placing autonomous WhatsApp VoIP call to %s (objective: '%s')",
            target,
            objective,
        )

        try:
            if on_status:
                try:
                    await on_status("DIALING")
                except Exception as status_err:
                    logger.debug(f"on_status initial callback error: {status_err}")

            process = await asyncio.create_subprocess_exec(
                args[0],
                *args[1:],
                cwd=str(self._extension_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_lines: List[str] = []
            stderr_bytes = b""

            if on_status and isinstance(process.stdout, asyncio.StreamReader):
                while True:
                    line_bytes = await process.stdout.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    stdout_lines.append(line)
                    line_lower = line.lower()
                    try:
                        if "remote phone is ringing" in line_lower or "ringing" in line_lower:
                            await on_status("RINGING")
                        elif (
                            "answered and connected" in line_lower
                            or "media channels open" in line_lower
                        ):
                            await on_status("IN_PROGRESS")
                        elif "call finished" in line_lower or "call ended" in line_lower:
                            await on_status("ENDED")
                    except Exception as cb_err:
                        logger.debug(f"Call event streaming callback error: {cb_err}")

                stderr_bytes = (
                    await process.stderr.read()
                    if isinstance(process.stderr, asyncio.StreamReader)
                    else b""
                )
                await process.wait()
                stdout_text = "\n".join(stdout_lines).strip()
            else:
                stdout_raw, stderr_bytes = await process.communicate()
                stdout_text = stdout_raw.decode("utf-8", errors="replace").strip()

            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

            result = self._parse_json_result(stdout_text)
            if result:
                return result

            if process.returncode != 0:
                logger.error("WhatsApp VoIP runner failed: %s", stderr_text)
                return WhatsAppCallResult(
                    success=False,
                    error=stderr_text or f"Process exited with code {process.returncode}",
                )

            return WhatsAppCallResult(
                success=False,
                error="Call completed but no structured result was captured.",
            )

        except Exception as exc:
            logger.exception("Failed to execute WhatsApp VoIP call: %s", exc)
            return WhatsAppCallResult(success=False, error=str(exc))

    async def login(
        self,
        force_refresh: bool = False,
        wait_for_scan: bool = False,
        timeout_seconds: int = 180,
    ) -> Dict[str, Any]:
        """Generate and display WhatsApp login QR code, awaiting device linking."""
        if not self.is_available:
            return {
                "success": False,
                "authenticated": False,
                "error": f"WhatsApp voice extension not found at {self._extension_dir}",
            }

        cmd = self._resolve_runner_command()
        args = [*cmd, "--action", "login", "--json"]
        if force_refresh:
            args.append("--force")

        env = os.environ.copy()
        if settings.GEMINI_API_KEY:
            env["GEMINI_API_KEY"] = settings.GEMINI_API_KEY
        env["GEMINI_MODEL"] = settings.MODEL_MAP_GEMINI_LIVE
        env["WHATSAPP_AUTH_DIR"] = str(self._auth_dir)
        env["WHATSAPP_DB_PATH"] = str(settings.WHATSAPP_DB_PATH)
        env["WORKSPACE_DIR"] = str(self._workspace_dir)
        env["USER_NAME"] = settings.USER_CALLSIGN
        env["USER_CALLSIGN"] = settings.USER_CALLSIGN
        env["APP_NAME"] = settings.APP_NAME
        env["WHATSAPP_CALL_LANGUAGE"] = settings.WHATSAPP_CALL_LANGUAGE
        env["DEFAULT_COUNTRY_CODE"] = settings.WHATSAPP_DEFAULT_COUNTRY_CODE

        logger.info("Initiating WhatsApp Web device authentication/login...")
        try:
            process = await asyncio.create_subprocess_exec(
                args[0],
                *args[1:],
                cwd=str(self._extension_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._login_process = process

            stdout_stream = process.stdout
            assert stdout_stream is not None
            qr_ready_event = asyncio.Event()
            qr_info: Dict[str, Any] = {}
            final_result: Optional[Dict[str, Any]] = None
            captured_lines: List[str] = []

            async def stdout_reader() -> None:
                nonlocal final_result, qr_info
                try:
                    while True:
                        line_bytes = await stdout_stream.readline()
                        if not line_bytes:
                            break
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        captured_lines.append(line)

                        if "[WHATSAPP_QR_JSON]" in line and "[/WHATSAPP_QR_JSON]" in line:
                            start = line.index("[WHATSAPP_QR_JSON]") + len("[WHATSAPP_QR_JSON]")
                            end = line.index("[/WHATSAPP_QR_JSON]")
                            try:
                                data = json.loads(line[start:end].strip())
                                if isinstance(data, dict):
                                    qr_info = data
                                    html_path = data.get("htmlPath")
                                    if html_path:
                                        try:
                                            import webbrowser

                                            webbrowser.open(Path(html_path).resolve().as_uri())
                                        except Exception:
                                            pass
                                        try:
                                            startfile_fn = getattr(os, "startfile", None)
                                            if callable(startfile_fn):
                                                startfile_fn(str(html_path))
                                        except Exception:
                                            pass
                                        print(
                                            "\n[JARVIS] WhatsApp Authentication QR Code generated!"
                                        )
                                        print(f"[JARVIS] View & Scan QR in browser: {html_path}")
                                        print(
                                            "[JARVIS] On phone: WhatsApp -> Settings -> Linked Devices -> Link a Device\n"
                                        )
                            except Exception as e:
                                logger.warning("Failed parsing [WHATSAPP_QR_JSON]: %s", e)
                            qr_ready_event.set()

                        if "[CALL_RESULT_JSON]" in line and "[/CALL_RESULT_JSON]" in line:
                            start = line.index("[CALL_RESULT_JSON]") + len("[CALL_RESULT_JSON]")
                            end = line.index("[/CALL_RESULT_JSON]")
                            try:
                                data = json.loads(line[start:end].strip())
                                if isinstance(data, dict):
                                    final_result = data
                            except Exception as e:
                                logger.warning("Failed parsing [CALL_RESULT_JSON]: %s", e)
                            qr_ready_event.set()

                        if (
                            "success" in line
                            and "authenticated" in line
                            and line.startswith("{")
                            and line.endswith("}")
                        ):
                            try:
                                data = json.loads(line)
                                if isinstance(data, dict):
                                    final_result = data
                                    qr_ready_event.set()
                            except Exception:
                                pass
                except Exception as read_err:
                    logger.debug("stdout_reader exception: %s", read_err)
                finally:
                    qr_ready_event.set()

            reader_task = asyncio.create_task(stdout_reader())

            if not wait_for_scan:
                try:
                    await asyncio.wait_for(qr_ready_event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    pass

                if final_result:
                    return final_result

                if qr_info:
                    html_p = qr_info.get(
                        "htmlPath",
                        str(self._workspace_dir / "data" / "whatsapp" / "login_qr.html"),
                    )
                    return {
                        "success": True,
                        "authenticated": False,
                        "status": "qr_ready",
                        "html_path": str(html_p),
                        "message": (
                            f"WhatsApp Web QR code has been generated and displayed in your browser at {html_p}. "
                            "Please scan it using WhatsApp on your phone (Settings > Linked Devices > Link a Device) to complete linking."
                        ),
                    }

                if process.returncode is not None and process.returncode != 0:
                    assert process.stderr is not None
                    err_bytes = await process.stderr.read()
                    err_msg = err_bytes.decode("utf-8", errors="replace").strip()
                    return {
                        "success": False,
                        "authenticated": False,
                        "error": err_msg or f"Process exited with code {process.returncode}",
                    }

            # If wait_for_scan is True (CLI mode), wait until device linking completes
            try:
                await asyncio.wait_for(reader_task, timeout=float(timeout_seconds))
                await process.wait()
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except Exception:
                    pass
                return {
                    "success": False,
                    "authenticated": False,
                    "error": f"WhatsApp authentication timed out after {timeout_seconds} seconds.",
                }

            if final_result:
                return final_result

            for line in reversed(captured_lines):
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        if isinstance(data, dict) and (
                            "success" in data or "authenticated" in data
                        ):
                            return data
                    except Exception:
                        continue

            if process.returncode == 0:
                return {
                    "success": True,
                    "authenticated": True,
                    "message": "WhatsApp device linked successfully.",
                }

            assert process.stderr is not None
            err_bytes = await process.stderr.read()
            err_msg = err_bytes.decode("utf-8", errors="replace").strip()
            return {
                "success": False,
                "authenticated": False,
                "error": err_msg or f"Authentication process exited with code {process.returncode}",
            }

        except Exception as exc:
            logger.exception("Failed executing WhatsApp login: %s", exc)
            return {"success": False, "authenticated": False, "error": str(exc)}

    def logout(self) -> Dict[str, Any]:
        """Purge persisted WhatsApp authentication session credentials."""
        purged_count = 0
        if self._auth_dir.exists():
            for item in self._auth_dir.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                        purged_count += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        purged_count += 1
                except Exception as e:
                    logger.warning("Error deleting auth file %s: %s", item, e)

        return {
            "success": True,
            "purged_files": purged_count,
            "message": "WhatsApp session logged out and authentication credentials cleared.",
        }

    async def warmup(self) -> None:
        """Silently connect to WhatsApp network in the background on boot if authenticated."""
        if not self.is_available or not self.has_persisted_session:
            return

        cmd = self._resolve_runner_command()
        args = [*cmd, "--action", "warmup"]

        env = os.environ.copy()
        if settings.GEMINI_API_KEY:
            env["GEMINI_API_KEY"] = settings.GEMINI_API_KEY
        env["WHATSAPP_AUTH_DIR"] = str(self._auth_dir)
        env["WHATSAPP_DB_PATH"] = str(settings.WHATSAPP_DB_PATH)
        env["WORKSPACE_DIR"] = str(self._workspace_dir)
        env["USER_NAME"] = settings.USER_CALLSIGN
        env["USER_CALLSIGN"] = settings.USER_CALLSIGN
        env["APP_NAME"] = settings.APP_NAME
        env["DEFAULT_COUNTRY_CODE"] = settings.WHATSAPP_DEFAULT_COUNTRY_CODE

        try:
            logger.debug("Silently warming up WhatsApp network connection...")
            proc = await asyncio.create_subprocess_exec(
                args[0],
                *args[1:],
                cwd=str(self._extension_dir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            self._warmup_process = proc
        except Exception as exc:
            logger.debug("Silent WhatsApp warmup failed to spawn: %s", exc)

    def _parse_json_result(self, stdout: str) -> Optional[WhatsAppCallResult]:
        """Extract structured result from process output."""
        # Check for [CALL_RESULT_JSON] marker
        if "[CALL_RESULT_JSON]" in stdout and "[/CALL_RESULT_JSON]" in stdout:
            start = stdout.index("[CALL_RESULT_JSON]") + len("[CALL_RESULT_JSON]")
            end = stdout.index("[/CALL_RESULT_JSON]")
            json_blob = stdout[start:end].strip()
            try:
                data = json.loads(json_blob)
                return self._build_call_result(data)
            except Exception as e:
                logger.warning("Failed parsing [CALL_RESULT_JSON] payload: %s", e)

        # Fallback to search lines for JSON
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    if "success" in data or "callId" in data:
                        return self._build_call_result(data)
                except Exception:
                    continue

        return None

    def _build_call_result(self, data: Dict[str, Any]) -> WhatsAppCallResult:
        """Construct a validated WhatsAppCallResult from raw JSON."""
        duration_ms = float(data.get("durationMs", 0.0))
        summary_path = data.get("summaryPath")
        transcript_path = data.get("transcriptPath")

        summary_text: Optional[str] = data.get("summary")
        recipient_reply: Optional[str] = data.get("recipientReply")

        if summary_path and Path(summary_path).exists():
            try:
                summary_data = json.loads(Path(summary_path).read_text(encoding="utf-8"))
                if not summary_text:
                    summary_text = summary_data.get("summary")
                if not recipient_reply:
                    recipient_reply = summary_data.get("recipientReply")

                # If still empty, parse transcript array directly
                if not recipient_reply or not summary_text:
                    transcript_entries = summary_data.get("transcript", [])
                    user_turns = [
                        str(t.get("text", "")).strip()
                        for t in transcript_entries
                        if t.get("role") == "user" and str(t.get("text", "")).strip()
                    ]
                    if user_turns and not recipient_reply:
                        recipient_reply = "; ".join(user_turns)
                    if not summary_text:
                        reply_hint = recipient_reply or "No verbal reply detected in call."
                        target_num = data.get("targetNumber", "")
                        summary_text = f"Call to {target_num} concluded after {round(duration_ms / 1000.0, 1)}s. Recipient replied: '{reply_hint}'"
            except Exception as e:
                logger.debug("Failed parsing summary_path for fallback reply: %s", e)

        if not recipient_reply and "transcript" in data and isinstance(data["transcript"], list):
            user_turns = [
                str(t.get("text", "")).strip()
                for t in data["transcript"]
                if t.get("role") == "user" and str(t.get("text", "")).strip()
            ]
            if user_turns:
                recipient_reply = "; ".join(user_turns)

        return WhatsAppCallResult(
            success=bool(data.get("success", False)),
            call_id=str(data.get("callId", "")),
            target_number=str(data.get("targetNumber", "")),
            duration_seconds=round(duration_ms / 1000.0, 2),
            transcript_path=transcript_path,
            summary_path=summary_path,
            summary_text=summary_text,
            recipient_reply=recipient_reply,
            error=data.get("error"),
        )

    def get_call_history(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieve recent completed WhatsApp calls from history store and transcripts."""
        combined_records: Dict[str, Dict[str, Any]] = {}

        history_candidates = [
            self._workspace_dir / "data" / "whatsapp" / "logs" / "call-history.json",
            self._extension_dir / "logs" / "call-history.json",
        ]

        for file_path in history_candidates:
            if file_path.exists():
                try:
                    records = json.loads(file_path.read_text(encoding="utf-8"))
                    if isinstance(records, list):
                        for rec in records:
                            cid = str(rec.get("callId", "")).strip()
                            if cid and cid not in combined_records:
                                combined_records[cid] = rec
                except Exception as e:
                    logger.warning("Error reading call history from %s: %s", file_path, e)

        # Also incorporate transcript JSON files from data/whatsapp/logs
        logs_dir = self._workspace_dir / "data" / "whatsapp" / "logs"
        if logs_dir.exists():
            try:
                transcript_files = sorted(
                    logs_dir.glob("transcript-*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for tf in transcript_files[:15]:
                    try:
                        data = json.loads(tf.read_text(encoding="utf-8"))
                        cid = str(data.get("callId", "")).strip()
                        if not cid:
                            continue
                        call_metrics = data.get("metrics", {}).get("call", {})
                        transcript_entries = data.get("transcript", [])
                        user_turns = [
                            str(t.get("text", "")).strip()
                            for t in transcript_entries
                            if t.get("role") == "user" and str(t.get("text", "")).strip()
                        ]
                        reply = data.get("recipientReply") or (
                            "; ".join(user_turns) if user_turns else "No verbal reply detected."
                        )
                        target_num = call_metrics.get("targetNumber") or data.get(
                            "targetNumber", "recipient"
                        )
                        summary = (
                            data.get("summary")
                            or f"Call to {target_num} completed. Reply: '{reply}'"
                        )

                        rec_data = {
                            "callId": cid,
                            "phoneNumber": target_num,
                            "targetName": target_num,
                            "recipientReply": reply,
                            "summary": summary,
                            "durationSec": str(
                                round(float(call_metrics.get("durationMs", 0)) / 1000.0, 1)
                            ),
                            "timestamp": data.get("timestamp") or tf.stat().st_mtime,
                            "callOutcome": call_metrics.get("endReason", "ended"),
                        }
                        # Transcript files take precedence or fill in missing
                        if cid not in combined_records or not combined_records[cid].get(
                            "recipientReply"
                        ):
                            combined_records[cid] = rec_data
                    except Exception:
                        continue
            except Exception as e:
                logger.debug("Fallback reading transcript files: %s", e)

        def _sort_key(item: Dict[str, Any]) -> float:
            ts = item.get("timestamp")
            if isinstance(ts, (int, float)):
                return float(ts) if ts > 1e11 else float(ts) * 1000.0
            if isinstance(ts, str):
                try:
                    from datetime import datetime

                    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000.0
                except Exception:
                    pass
            return 0.0

        sorted_records = sorted(combined_records.values(), key=_sort_key, reverse=True)
        return sorted_records[:limit]

    def get_latest_call(self) -> Optional[Dict[str, Any]]:
        """Retrieve the most recent completed call."""
        history = self.get_call_history(limit=1)
        return history[0] if history else None

    def search_call_history(self, query: str) -> List[Dict[str, Any]]:
        """Search past calls by keyword."""
        all_calls = self.get_call_history(limit=50)
        q = query.lower()
        results: List[Dict[str, Any]] = []

        for call in all_calls:
            text_corpus = (
                f"{call.get('target', '')} {call.get('objective', '')} "
                f"{call.get('summary', '')} {call.get('recipientReply', '')}"
            ).lower()
            if q in text_corpus:
                results.append(call)

        return results

    async def _execute_runner_action(self, action_args: List[str]) -> Dict[str, Any]:
        """Execute a sub-action on the unified Node.js WhatsApp runner."""
        if not self.is_available:
            return {
                "success": False,
                "error": f"WhatsApp runner not found at {self._extension_dir}",
            }

        cmd = self._resolve_runner_command()
        args = [*cmd, *action_args, "--json"]

        env = os.environ.copy()
        if settings.GEMINI_API_KEY:
            env["GEMINI_API_KEY"] = settings.GEMINI_API_KEY
        env["WHATSAPP_AUTH_DIR"] = str(self._auth_dir)
        env["WHATSAPP_DB_PATH"] = str(settings.WHATSAPP_DB_PATH)
        env["WORKSPACE_DIR"] = str(self._workspace_dir)
        env["DEFAULT_COUNTRY_CODE"] = settings.WHATSAPP_DEFAULT_COUNTRY_CODE

        try:
            process = await asyncio.create_subprocess_exec(
                args[0],
                *args[1:],
                cwd=str(self._extension_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout_raw, stderr_raw = await process.communicate()
            stdout_text = stdout_raw.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_raw.decode("utf-8", errors="replace").strip()

            if "[CALL_RESULT_JSON]" in stdout_text and "[/CALL_RESULT_JSON]" in stdout_text:
                start = stdout_text.index("[CALL_RESULT_JSON]") + len("[CALL_RESULT_JSON]")
                end = stdout_text.index("[/CALL_RESULT_JSON]")
                parsed = json.loads(stdout_text[start:end].strip())
                if isinstance(parsed, dict):
                    return parsed
                return {"success": True, "data": parsed}

            for line in reversed(stdout_text.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed_line = json.loads(line)
                        if isinstance(parsed_line, dict):
                            return parsed_line
                        return {"success": True, "data": parsed_line}
                    except Exception:
                        pass

            if process.returncode != 0:
                return {
                    "success": False,
                    "error": stderr_text or f"Exited with code {process.returncode}",
                }

            return {"success": True, "raw_output": stdout_text}
        except Exception as exc:
            logger.exception("Error executing WhatsApp action %s: %s", action_args, exc)
            return {"success": False, "error": str(exc)}

    async def get_diagnostics(self) -> Dict[str, Any]:
        """Fetch database stats, connection state, and event counters from unified WhatsApp runner."""
        return await self._execute_runner_action(["--action", "diagnostics"])

    async def sync(self, full: bool = False, settle_ms: int = 4000) -> Dict[str, Any]:
        """Trigger one-shot synchronization of WhatsApp contacts, chats, and messages."""
        cmd = ["--action", "sync", "--settle", str(settle_ms)]
        if full:
            cmd.append("--full")
        return await self._execute_runner_action(cmd)

    async def send_text(
        self,
        recipient: str = "",
        message: str = "",
        reply_to: Optional[str] = None,
        target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a WhatsApp text message to a recipient."""
        to_dest = recipient or target or ""
        args = ["--action", "send-text", "--to", to_dest, "--message", message]
        if reply_to:
            args.extend(["--reply-to", reply_to])
        return await self._execute_runner_action(args)

    async def send_file(
        self,
        recipient: str = "",
        file_path: str = "",
        caption: Optional[str] = None,
        file_as: Optional[str] = None,
        target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a WhatsApp document or media file to a recipient."""
        to_dest = recipient or target or ""
        args = ["--action", "send-file", "--to", to_dest, "--file", str(file_path)]
        if caption:
            args.extend(["--caption", caption])
        if file_as:
            args.extend(["--as", file_as])
        return await self._execute_runner_action(args)

    async def send_voice(
        self,
        recipient: str = "",
        audio_path: str = "",
        file_path: Optional[str] = None,
        target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a WhatsApp voice note (PTT) to a recipient."""
        to_dest = recipient or target or ""
        sound_path = audio_path or file_path or ""
        return await self._execute_runner_action(
            ["--action", "send-voice", "--to", to_dest, "--file", str(sound_path)]
        )

    async def send_reaction(
        self,
        chat_jid: str = "",
        message_id: str = "",
        reaction: str = "",
        target: Optional[str] = None,
        msg_id: Optional[str] = None,
        emoji: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send an emoji reaction to a WhatsApp message."""
        to_chat = chat_jid or target or ""
        mid = message_id or msg_id or ""
        react = reaction or emoji or ""
        return await self._execute_runner_action(
            ["--action", "send-reaction", "--to", to_chat, "--id", mid, "--emoji", react]
        )

    async def check_number(self, phone: str) -> Dict[str, Any]:
        """Check whether a phone number is registered on WhatsApp."""
        return await self._execute_runner_action(["--action", "check-number", "--phone", phone])

    async def mark_read(self, chat_jid: str) -> Dict[str, Any]:
        """Mark a WhatsApp conversation as read."""
        return await self._execute_runner_action(["--action", "mark-read", "--chat", chat_jid])

    async def download_media(
        self, chat_jid: str, msg_id: str, target_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Download decrypted media for an incoming message."""
        cmd = ["--action", "download-media", "--chat", chat_jid, "--id", msg_id]
        if target_path:
            cmd.extend(["--file", target_path])
        return await self._execute_runner_action(cmd)

    def resolve_contact(self, name_or_number: str) -> Dict[str, Any]:
        """Resolve a contact name or number using JARVIS contact resolver and fallback contacts."""
        try:
            from jarvis.execution.whatsapp.resolver import jarvis_contact_resolver

            res = jarvis_contact_resolver.resolve(name_or_number)
            if res.resolved and res.phone:
                return {
                    "name": res.display_name or name_or_number,
                    "phone_number": res.phone,
                    "formatted": f"+{res.phone}",
                }
        except Exception as e:
            logger.debug(f"JARVIS contact resolver fallback notice in caller: {e}")

        contacts_file = (
            settings.WHATSAPP_CONTACTS_FILE
            or self._workspace_dir / "data" / "whatsapp" / "contacts.json"
        )
        target = name_or_number.strip()
        lower_target = target.lower()

        if contacts_file.exists():
            try:
                known = json.loads(contacts_file.read_text(encoding="utf-8"))
                if isinstance(known, dict) and lower_target in known:
                    digits = "".join(filter(str.isdigit, str(known[lower_target])))
                    return {
                        "name": target,
                        "phone_number": digits,
                        "formatted": f"+{digits}",
                    }
            except Exception:
                pass

        # Parse digits
        digits_only = "".join(filter(str.isdigit, target))
        if len(digits_only) == 10 and digits_only[0] in "6789":
            digits_only = f"{settings.WHATSAPP_DEFAULT_COUNTRY_CODE}{digits_only}"

        if 7 <= len(digits_only) <= 15:
            return {
                "name": None,
                "phone_number": digits_only,
                "formatted": f"+{digits_only}",
            }

        return {
            "name": target,
            "phone_number": digits_only or target,
            "formatted": target,
        }


# Global instance
_whatsapp_caller: Optional[WhatsAppVoiceCaller] = None


def get_whatsapp_caller() -> WhatsAppVoiceCaller:
    """Retrieve or create the WhatsAppVoiceCaller singleton."""
    global _whatsapp_caller
    if _whatsapp_caller is None:
        _whatsapp_caller = WhatsAppVoiceCaller()
    return _whatsapp_caller
