"""
voice_service.meeting_audio_worker — Bidirectional audio bridge.

Manages the real-time audio bridge between the ACS media streaming
WebSocket and the Azure OpenAI Realtime API WebSocket.  Handles
audio format conversion, speaker tracking, transcript persistence,
barge-in, and tool execution.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp

from aida_sdk.clients.acs_client import ACSClient
from aida_sdk.clients.realtime_client import RealtimeClient
from aida_sdk.config import settings

from voice_service.voice_state import VoiceSession
from voice_service.voice_tools import VOICE_TOOLS, execute_tool
from voice_service.meeting_wake_word import WakeWordDetector
from voice_service.meeting_state import MeetingSessionManager

logger = logging.getLogger(__name__)

# ACS media streaming sends 24kHz 16-bit mono PCM
ACS_SAMPLE_RATE = 24000
ACS_BYTES_PER_SAMPLE = 2

# Persist transcript to data service every N entries
TRANSCRIPT_PERSIST_INTERVAL = 5


@dataclass
class CallContext:
    """
    Shared mutable state for a single call's audio processing loops.

    This inner class is passed between the ACS-to-Realtime and
    Realtime-to-ACS coroutines so they can coordinate barge-in,
    track the current speaker, and accumulate transcript text.
    """

    is_speaking: bool = False
    """True while the Realtime API is generating audio output."""
    current_response_id: str = ""
    """ID of the Realtime API response currently being streamed."""
    current_item_id: str = ""
    """ID of the current conversation item (for barge-in truncation)."""
    accumulated_text: str = ""
    """Accumulated assistant response text (for transcript)."""
    pending_tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    """In-flight tool calls keyed by call_id."""
    last_speaker_raw_id: str = ""
    """Raw participant ID of the last detected speaker."""
    entries_since_persist: int = 0
    """Counter for periodic transcript persistence."""


class MeetingAudioWorker:
    """
    Manages the bidirectional audio bridge for a single voice session.

    Lifecycle:
      1. ``start(call_connection_id)`` — connects to Realtime API,
         spawns the two async loops.
      2. Audio flows: ACS WS -> Realtime API -> ACS WS.
      3. ``stop()`` — cancels loops, closes connections, persists transcript.
    """

    def __init__(
        self,
        session: VoiceSession,
        acs_client: ACSClient,
        meeting_manager: MeetingSessionManager,
    ) -> None:
        self._session = session
        self._acs_client = acs_client
        self._meeting_manager = meeting_manager
        self._realtime_client = RealtimeClient()
        self._wake_word = WakeWordDetector()
        self._ctx = CallContext()

        self._acs_to_realtime_task: asyncio.Task | None = None
        self._realtime_to_acs_task: asyncio.Task | None = None
        self._running = False

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self, call_connection_id: str | None = None) -> None:
        """
        Connect to the Realtime API and begin audio bridging.

        Args:
            call_connection_id: Optional ACS call connection ID (may be
                set later when the first ACS metadata message arrives).
        """
        if call_connection_id:
            self._session.call_connection_id = call_connection_id

        logger.info(
            "Starting audio worker: session=%s, call=%s",
            self._session.session_id,
            self._session.call_connection_id,
        )

        # Build system instructions
        instructions = self._build_instructions()

        # Connect to the OpenAI Realtime API
        await self._realtime_client.connect(
            instructions=instructions,
            tools=VOICE_TOOLS,
        )

        self._running = True

        # TODO: Spawn the two bridging loops as background tasks
        # self._realtime_to_acs_task = asyncio.create_task(self._realtime_to_acs_loop())
        # The ACS->Realtime direction is driven by handle_acs_message/handle_acs_audio

        # Start listening for Realtime API events
        self._realtime_to_acs_task = asyncio.create_task(self._realtime_to_acs_loop())

        logger.info("Audio worker started: session=%s", self._session.session_id)

    async def stop(self) -> None:
        """Cancel loops, close Realtime API connection, persist transcript."""
        self._running = False

        # Cancel background tasks
        for task in [self._acs_to_realtime_task, self._realtime_to_acs_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Persist final transcript
        await self._persist_transcript()

        # Close Realtime API connection
        await self._realtime_client.close()

        # Trigger post-processing if this was a meeting
        if self._session.meeting_id:
            await self._meeting_manager.end_session(self._session.meeting_id)

        logger.info("Audio worker stopped: session=%s", self._session.session_id)

    # ── ACS Message Handling ─────────────────────────────────────────

    async def handle_acs_message(self, data: str) -> None:
        """
        Handle a text message from the ACS media streaming WebSocket.

        ACS sends JSON metadata messages for events like streaming
        started/stopped, and audio data as base64-encoded PCM.

        Args:
            data: Raw text message from the ACS WebSocket.
        """
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("Non-JSON ACS message: %s", data[:100])
            return

        kind = message.get("kind", "")

        if kind == "AudioMetadata":
            # Extract audio format info
            # TODO: Validate sample rate matches our expected 24kHz/16-bit
            logger.info("ACS AudioMetadata received: %s", json.dumps(message.get("audioMetadata", {})))

        elif kind == "AudioData":
            # Forward audio to Realtime API
            audio_data = message.get("audioData", {})
            audio_b64 = audio_data.get("data", "")
            if audio_b64:
                audio_bytes = base64.b64decode(audio_b64)
                await self._forward_audio_to_realtime(audio_bytes)

                # Track speaker if participant info is present
                participant_raw_id = audio_data.get("participantRawId", "")
                if participant_raw_id:
                    self._ctx.last_speaker_raw_id = participant_raw_id

        elif kind == "StoppedMediaStreaming":
            logger.info("ACS media streaming stopped")
            await self.stop()

        else:
            logger.debug("Unhandled ACS message kind: %s", kind)

    async def handle_acs_audio(self, data: bytes) -> None:
        """
        Handle a binary audio frame from the ACS WebSocket.

        Args:
            data: Raw PCM audio bytes.
        """
        await self._forward_audio_to_realtime(data)

    # ── ACS -> Realtime ──────────────────────────────────────────────

    async def _forward_audio_to_realtime(self, audio_bytes: bytes) -> None:
        """
        Forward PCM audio from ACS to the Realtime API.

        In meeting mode, audio is only forwarded when voice is active
        (wake word detected).  In direct call mode, audio is always
        forwarded.

        Args:
            audio_bytes: Raw PCM16 audio data from ACS.
        """
        if not self._running:
            return

        # In meeting mode, only forward when voice is active (wake word detected)
        if self._session.is_meeting_mode and not self._session.is_voice_active:
            # TODO: Still transcribe for meeting notes, just don't send to Realtime API
            return

        # Handle barge-in: if user starts speaking while AIDA is responding,
        # send StopAudio to ACS and truncate the Realtime API response
        # TODO: Implement barge-in detection using VAD energy levels
        # TODO: Send conversation.item.truncate to Realtime API
        # TODO: Send StopAudio to ACS WebSocket

        try:
            await self._realtime_client.send_audio(audio_bytes)
        except Exception:
            logger.exception("Failed to forward audio to Realtime API")

    # ── Realtime -> ACS ──────────────────────────────────────────────

    async def _realtime_to_acs_loop(self) -> None:
        """
        Read events from the Realtime API and route them appropriately.

        Audio delta events are forwarded to the ACS WebSocket.
        Tool call events trigger tool execution.
        Transcript events are accumulated for persistence.
        """
        try:
            async for event in self._realtime_client.receive_events():
                if not self._running:
                    break
                await self._handle_realtime_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Realtime-to-ACS loop error: session=%s", self._session.session_id)

    async def _handle_realtime_event(self, event: dict[str, Any]) -> None:
        """
        Dispatch a single Realtime API event.

        Args:
            event: Parsed JSON event from the Realtime API WebSocket.
        """
        event_type = event.get("type", "")

        # ── Session events ───────────────────────────────────────────
        if event_type == "session.created":
            logger.info("Realtime session created: %s", event.get("session", {}).get("id", ""))

        elif event_type == "session.updated":
            logger.debug("Realtime session updated")

        # ── Audio output ─────────────────────────────────────────────
        elif event_type == "response.audio.delta":
            # Forward audio to ACS WebSocket
            audio_b64 = event.get("delta", "")
            if audio_b64 and self._session.acs_ws:
                await self._send_audio_to_acs(audio_b64)
            self._ctx.is_speaking = True

        elif event_type == "response.audio.done":
            self._ctx.is_speaking = False

        # ── Text output (for transcript) ─────────────────────────────
        elif event_type == "response.audio_transcript.delta":
            self._ctx.accumulated_text += event.get("delta", "")

        elif event_type == "response.audio_transcript.done":
            transcript_text = event.get("transcript", self._ctx.accumulated_text)
            if transcript_text.strip():
                self._session.add_transcript_entry("AIDA", transcript_text.strip())
                self._ctx.entries_since_persist += 1
                await self._maybe_persist_transcript()
            self._ctx.accumulated_text = ""

        # ── User speech transcript ───────────────────────────────────
        elif event_type == "conversation.item.input_audio_transcription.completed":
            user_text = event.get("transcript", "")
            if user_text.strip():
                speaker = self._session.get_speaker_name(self._ctx.last_speaker_raw_id)
                self._session.add_transcript_entry(speaker, user_text.strip())
                self._ctx.entries_since_persist += 1

                # Check for wake word in meeting mode
                if self._session.is_meeting_mode:
                    self._wake_word.check_transcript(user_text)

                await self._maybe_persist_transcript()

        # ── Tool calls ───────────────────────────────────────────────
        elif event_type == "response.function_call_arguments.done":
            await self._handle_tool_call(event)

        # ── Response lifecycle ───────────────────────────────────────
        elif event_type == "response.created":
            self._ctx.current_response_id = event.get("response", {}).get("id", "")

        elif event_type == "response.done":
            self._ctx.current_response_id = ""
            self._ctx.current_item_id = ""

        # ── Error handling ───────────────────────────────────────────
        elif event_type == "error":
            logger.error("Realtime API error: %s", event.get("error", {}))

        else:
            logger.debug("Unhandled Realtime event: %s", event_type)

    # ── Tool Execution ───────────────────────────────────────────────

    async def _handle_tool_call(self, event: dict[str, Any]) -> None:
        """
        Execute a tool call from the Realtime API and return the result.

        Args:
            event: The function_call_arguments.done event.
        """
        call_id = event.get("call_id", "")
        tool_name = event.get("name", "")
        arguments_str = event.get("arguments", "{}")

        logger.info("Tool call: name=%s, call_id=%s", tool_name, call_id)

        try:
            args = json.loads(arguments_str)
        except json.JSONDecodeError:
            args = {}

        try:
            result = await execute_tool(tool_name, args, self._session)
            result_str = json.dumps(result) if isinstance(result, dict) else str(result)
        except Exception:
            logger.exception("Tool execution failed: %s", tool_name)
            result_str = json.dumps({"error": f"Tool '{tool_name}' execution failed"})

        # Send the tool result back to the Realtime API
        # TODO: Use the RealtimeClient to send conversation.item.create
        #       with type=function_call_output and trigger response.create
        if self._realtime_client._ws and not self._realtime_client._ws.closed:
            await self._realtime_client._ws.send_json({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result_str,
                },
            })
            await self._realtime_client._ws.send_json({"type": "response.create"})

    # ── Audio Output to ACS ──────────────────────────────────────────

    async def _send_audio_to_acs(self, audio_b64: str) -> None:
        """
        Send base64-encoded audio to the ACS WebSocket.

        Args:
            audio_b64: Base64-encoded PCM16 audio data from Realtime API.
        """
        if not self._session.acs_ws or self._session.acs_ws.closed:
            return

        # ACS expects audio data wrapped in a JSON message
        # TODO: Verify the exact ACS media streaming send format
        message = json.dumps({
            "kind": "AudioData",
            "audioData": {
                "data": audio_b64,
            },
        })
        try:
            await self._session.acs_ws.send_str(message)
        except Exception:
            logger.exception("Failed to send audio to ACS WebSocket")

    # ── Transcript Persistence ───────────────────────────────────────

    async def _maybe_persist_transcript(self) -> None:
        """Persist transcript periodically to avoid data loss on crash."""
        if self._ctx.entries_since_persist >= TRANSCRIPT_PERSIST_INTERVAL:
            await self._persist_transcript()
            self._ctx.entries_since_persist = 0

    async def _persist_transcript(self) -> None:
        """
        Persist the current transcript to the data service.

        Called periodically during the call (every TRANSCRIPT_PERSIST_INTERVAL
        entries) and once at call end.  This ensures transcript data survives
        crashes — the CallDisconnected event may fire before the worker
        finishes processing.
        """
        if not self._session.transcript_entries:
            return

        # TODO: POST to aida-data service:
        #   POST {DATA_SERVICE_URL}/api/transcripts/{meeting_id}
        #   Body: { "entries": [...], "session_id": ..., "is_final": false }
        logger.info(
            "Persisting transcript: session=%s, entries=%d",
            self._session.session_id,
            len(self._session.transcript_entries),
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _build_instructions(self) -> str:
        """Build system instructions for the Realtime API session."""
        base = (
            "You are AIDA, an AI digital assistant built by the AIDA team. "
            "You are participating in a voice call. Be concise, helpful, and "
            "natural in your responses. Speak conversationally — avoid bullet "
            "points and markdown formatting since this is a voice conversation.\n\n"
        )

        if self._session.is_meeting_mode:
            base += (
                "You are in a Teams meeting. Listen to the conversation and "
                "respond when addressed directly (your name is AIDA). "
                "You can help with meeting notes, action items, scheduling, "
                "and answering questions.\n"
            )
        else:
            base += (
                "You are on a direct call. The caller is speaking to you "
                "directly. Help them with whatever they need — scheduling, "
                "email drafts, knowledge search, meeting notes, and more.\n"
            )

        # TODO: Inject caller name, meeting subject, and recent context
        # TODO: Load user preferences from data service

        return base
