"""Google GenAI Realtime Voice Provider Adapter.

Implements RealtimeModelAdapter using official google-genai Live API client.
Handles bidirectional streaming, 16kHz audio input, 24kHz audio output,
function calling, session resumption, context compression, and GoAway signals.
"""

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from google import genai
from google.genai import types as genai_types

from jarvis.core.config import get_settings
from jarvis.core.exceptions import ModelProviderError
from jarvis.core.gateway.realtime import (
    LiveAudioChunk,
    LiveEvent,
    LiveEventType,
    LiveGoAway,
    LiveInteractionStatus,
    LiveResumptionUpdate,
    LiveSessionConfig,
    LiveToolCall,
    LiveToolResponse,
    LiveTranscription,
    RealtimeModelAdapter,
)
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class GoogleRealtimeAdapter(RealtimeModelAdapter):
    """Adapter for Google Gemini Live API models (gemini-3.8-live, gemini-3.8-live-extended-thinking)."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key and settings.GEMINI_API_KEY:
            key = settings.GEMINI_API_KEY.get_secret_value()
        if not key:
            raise ModelProviderError("GEMINI_API_KEY is not configured in environment or settings.")
        self._client = genai.Client(api_key=key)
        self._session: Any = None
        self._connect_cm: Any = None
        self._latest_resumption_handle: str | None = None
        self._active_model_id: str = "gemini-3.8-live"

    def _normalize_model_name(self, model_id: str) -> str:
        """Strip 'models/' prefix if present for uniform SDK invocation."""
        if model_id.startswith("models/"):
            return model_id[7:]
        return model_id

    async def connect(self, config: LiveSessionConfig) -> None:
        """Establish bidirectional realtime connection to Gemini Live API."""
        self._active_model_id = self._normalize_model_name(config.model_id)

        # 1. Build Speech Config
        speech_config = genai_types.SpeechConfig(
            voice_config=genai_types.VoiceConfig(
                prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                    voice_name=config.voice_name or "Puck"
                )
            )
        )

        # 2. Build LiveConnectConfig
        config_kwargs: dict[str, Any] = {
            "response_modalities": config.response_modalities or ["AUDIO"],
            "speech_config": speech_config,
        }

        if config.system_instruction:
            config_kwargs["system_instruction"] = config.system_instruction

        if config.tools:
            if "extended-thinking" in self._active_model_id:
                sanitized_tools = []
                for tool_item in config.tools:
                    if isinstance(tool_item, dict) and "function_declarations" in tool_item:
                        sanitized_decls = []
                        for decl in tool_item["function_declarations"]:
                            if isinstance(decl, dict):
                                clean_decl = dict(decl)
                                if clean_decl.get("behavior") == "BLOCKING":
                                    clean_decl["behavior"] = "NON_BLOCKING"
                                sanitized_decls.append(clean_decl)
                            else:
                                sanitized_decls.append(decl)
                        sanitized_tools.append({"function_declarations": sanitized_decls})
                    else:
                        sanitized_tools.append(tool_item)
                config_kwargs["tools"] = sanitized_tools
            else:
                config_kwargs["tools"] = config.tools

        if config.resumption_handle:
            config_kwargs["session_resumption"] = genai_types.SessionResumptionConfig(
                handle=config.resumption_handle,
                transparent=True,
            )

        if config.enable_compression:
            config_kwargs["context_window_compression"] = (
                genai_types.ContextWindowCompressionConfig()
            )

        thinking_level = getattr(config, "thinking_level", None)
        if (
            "extended-thinking" in self._active_model_id or "thinking" in self._active_model_id
        ) and not thinking_level:
            thinking_level = "LOW"

        if thinking_level or config.thinking_budget is not None:
            t_kwargs: dict[str, Any] = {"include_thoughts": True}
            if config.thinking_budget is not None:
                t_kwargs["thinking_budget"] = config.thinking_budget
            if thinking_level:
                level_enum = getattr(
                    genai_types.ThinkingLevel,
                    thinking_level.upper(),
                    genai_types.ThinkingLevel.LOW,
                )
                t_kwargs["thinking_level"] = level_enum
            config_kwargs["thinking_config"] = genai_types.ThinkingConfig(**t_kwargs)

        if config.input_transcription:
            config_kwargs["input_audio_transcription"] = genai_types.AudioTranscriptionConfig()

        if config.output_transcription:
            config_kwargs["output_audio_transcription"] = genai_types.AudioTranscriptionConfig()

        live_config = genai_types.LiveConnectConfig(**config_kwargs)

        try:
            logger.info("connecting_gemini_live", model=self._active_model_id)
            self._connect_cm = self._client.aio.live.connect(
                model=self._active_model_id,
                config=live_config,
            )
            self._session = await self._connect_cm.__aenter__()
        except Exception as exc:
            logger.error(
                "gemini_live_connection_failed", model=self._active_model_id, error=str(exc)
            )
            raise ModelProviderError(f"Failed to connect to Gemini Live API: {exc}") from exc

    async def send_text(self, text: str, end_of_turn: bool = True) -> None:
        """Send conversational text input into active realtime session."""
        if not self._session:
            raise ModelProviderError("Cannot send text: Live session is not connected.")

        if hasattr(self._session, "send_client_content"):
            turn = genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=text)],
            )
            await self._session.send_client_content(turns=[turn], turn_complete=end_of_turn)
        elif hasattr(self._session, "send_realtime_input"):
            await self._session.send_realtime_input(text=text)
        else:
            content = genai_types.LiveClientContent(
                turns=[
                    genai_types.Content(
                        role="user",
                        parts=[genai_types.Part.from_text(text=text)],
                    )
                ],
                turn_complete=end_of_turn,
            )
            await self._session.send(input=content, end_of_turn=end_of_turn)

    async def send_audio(self, pcm_data: bytes, end_of_turn: bool = False) -> None:
        """Send raw 16kHz 16-bit mono little-endian PCM audio chunk to model."""
        if not self._session:
            raise ModelProviderError("Cannot send audio: Live session is not connected.")

        if hasattr(self._session, "send_realtime_input"):
            audio_blob = genai_types.Blob(
                data=pcm_data,
                mime_type="audio/pcm;rate=16000",
            )
            await self._session.send_realtime_input(media=audio_blob)
            if end_of_turn:
                await self._session.send_realtime_input(audio_stream_end=True)
        else:
            realtime_input = genai_types.LiveClientRealtimeInput(
                media_chunks=[
                    genai_types.Blob(
                        data=pcm_data,
                        mime_type="audio/pcm;rate=16000",
                    )
                ]
            )
            await self._session.send(input=realtime_input, end_of_turn=end_of_turn)

    async def send_tool_response(self, response: LiveToolResponse) -> None:
        """Send verified function response back to the voice model."""
        if not self._session:
            raise ModelProviderError("Cannot send tool response: Live session is not connected.")

        payload = response.response
        if not isinstance(payload, dict) or ("output" not in payload and "error" not in payload):
            payload = {"output": payload}

        fn_response = genai_types.FunctionResponse(
            name=response.name,
            id=response.call_id,
            response=payload,
        )
        if hasattr(self._session, "send_tool_response"):
            await self._session.send_tool_response(function_responses=[fn_response])
        else:
            client_tool_response = genai_types.LiveClientToolResponse(
                function_responses=[fn_response]
            )
            await self._session.send(input=client_tool_response)

    async def interrupt(self) -> None:
        """Signal client barge-in / interruption to truncate active speech generation."""
        if not self._session:
            return
        if hasattr(self._session, "send_client_content"):
            await self._session.send_client_content(turns=[], turn_complete=True)
        else:
            client_content = genai_types.LiveClientContent(turns=[], turn_complete=True)
            await self._session.send(input=client_content, end_of_turn=True)

    async def close(self) -> None:
        """Gracefully terminate connection and release local resources."""
        if self._connect_cm:
            try:
                await self._connect_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("error_exiting_live_context", error=str(e))
            finally:
                self._connect_cm = None
                self._session = None
        elif self._session:
            try:
                await self._session.close()
            except Exception as e:
                logger.warning("error_closing_live_session", error=str(e))
            finally:
                self._session = None

    def get_latest_resumption_handle(self) -> str | None:
        """Retrieve most recent valid session resumption token."""
        return self._latest_resumption_handle

    @property
    def connected(self) -> bool:
        """Return True if the underlying session connection is active."""
        return self._session is not None

    async def receive_events(self) -> AsyncIterator[LiveEvent]:
        """Asynchronous stream of incoming server events (audio, text, tools, GoAway)."""
        if not self._session:
            raise ModelProviderError("Cannot receive events: Live session is not connected.")

        try:
            while self.connected and self._session is not None:
                message_received = False
                async for message in self._session.receive():
                    message_received = True
                    # 1. Process tool calls FIRST before server turn completion signals
                    has_tool_calls = bool(message.tool_call and message.tool_call.function_calls)
                    if has_tool_calls and message.tool_call:
                        for fc in message.tool_call.function_calls:
                            tool_call = LiveToolCall(
                                call_id=fc.id or f"call_{uuid4().hex[:8]}",
                                name=fc.name or "unknown",
                                arguments=fc.args or {},
                                is_non_blocking=True,
                            )
                            yield LiveEvent(
                                event_type=LiveEventType.TOOL_CALL,
                                payload=tool_call,
                            )

                    # 2. Process server content (speech, text, turn completions)
                    if message.server_content:
                        sc = message.server_content

                        if sc.interrupted:
                            yield LiveEvent(
                                event_type=LiveEventType.INTERRUPTED,
                                payload={"interrupted": True},
                            )

                        if sc.model_turn and sc.model_turn.parts:
                            for part in sc.model_turn.parts:
                                if getattr(part, "thought", False):
                                    if part.text:
                                        yield LiveEvent(
                                            event_type=LiveEventType.STATUS_CHANGE,
                                            payload={
                                                "status": LiveInteractionStatus.IN_PROGRESS.value,
                                                "thought": part.text,
                                            },
                                        )
                                    continue
                                if part.text:
                                    yield LiveEvent(
                                        event_type=LiveEventType.TEXT_DELTA,
                                        payload=part.text,
                                    )
                                if part.inline_data and part.inline_data.data:
                                    chunk = LiveAudioChunk(
                                        data=part.inline_data.data,
                                        sample_rate=24000,
                                        channels=1,
                                        format="audio/pcm",
                                    )
                                    yield LiveEvent(
                                        event_type=LiveEventType.AUDIO_CHUNK,
                                        payload=chunk,
                                    )

                        # Realtime speech output transcription (from model)
                        if sc.output_transcription and sc.output_transcription.text:
                            yield LiveEvent(
                                event_type=LiveEventType.TEXT_DELTA,
                                payload=sc.output_transcription.text,
                            )

                        # Realtime speech input transcription (from user mic)
                        if sc.input_transcription and sc.input_transcription.text:
                            yield LiveEvent(
                                event_type=LiveEventType.TRANSCRIPTION,
                                payload=LiveTranscription(
                                    text=sc.input_transcription.text,
                                    is_user=True,
                                ),
                            )

                        # Only yield TURN_COMPLETE if no tool calls are pending in this message.
                        # Tool call proposals require client tool execution before conversational turn completes.
                        if sc.turn_complete and not has_tool_calls:
                            yield LiveEvent(
                                event_type=LiveEventType.TURN_COMPLETE,
                                payload={"turn_complete": True},
                            )

                        if sc.generation_complete and not has_tool_calls:
                            yield LiveEvent(
                                event_type=LiveEventType.GENERATION_COMPLETE,
                                payload={"generation_complete": True},
                            )

                    # 3. Process tool call cancellations
                    if message.tool_call_cancellation and message.tool_call_cancellation.ids:
                        yield LiveEvent(
                            event_type=LiveEventType.TOOL_CALL_CANCEL,
                            payload=message.tool_call_cancellation.ids,
                        )

                    # 4. Process GoAway warnings
                    if message.go_away:
                        time_left_str = message.go_away.time_left
                        time_left = float(time_left_str.rstrip("s")) if time_left_str else None
                        go_away = LiveGoAway(
                            time_left_seconds=time_left,
                            reason="PROVIDER_SCHEDULED_ROTATION",
                        )
                        yield LiveEvent(
                            event_type=LiveEventType.GO_AWAY,
                            payload=go_away,
                        )

                    # 5. Process Session Resumption Updates
                    if message.session_resumption_update:
                        s_up = message.session_resumption_update
                        if s_up.new_handle:
                            self._latest_resumption_handle = s_up.new_handle
                        resumption_event = LiveResumptionUpdate(
                            handle=s_up.new_handle or (self._latest_resumption_handle or ""),
                            resumable=s_up.resumable if s_up.resumable is not None else True,
                            last_consumed_message_index=s_up.last_consumed_client_message_index,
                        )
                        yield LiveEvent(
                            event_type=LiveEventType.RESUMPTION_UPDATE,
                            payload=resumption_event,
                        )

                if not message_received:
                    break

        except Exception as exc:
            logger.error("error_in_live_event_stream", error=str(exc))
            yield LiveEvent(
                event_type=LiveEventType.ERROR,
                payload={"error": str(exc)},
            )
