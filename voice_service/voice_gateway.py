"""
voice_service.voice_gateway — WebSocket gateway for voice sessions.

Accepts WebSocket upgrades on ``/voice-v2``, creates a VoiceSession for
each connection, spawns a MeetingAudioWorker, and proxies audio frames
between the Teams/ACS client and the OpenAI Realtime API.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import aiohttp
from aiohttp import web
from aiohttp.web import Request, WebSocketResponse

from aida_sdk.clients.acs_client import ACSClient

from voice_service.voice_state import VoiceSession
from voice_service.meeting_audio_worker import MeetingAudioWorker
from voice_service.meeting_state import MeetingSessionManager

logger = logging.getLogger(__name__)


class VoiceGateway:
    """
    Manages WebSocket connections for active voice sessions.

    Each incoming WebSocket connection represents an ACS media stream.
    The gateway creates a VoiceSession, attaches a MeetingAudioWorker
    to bridge audio between ACS and the Realtime API, and manages the
    lifecycle until the call disconnects.
    """

    def __init__(
        self,
        acs_client: ACSClient,
        meeting_manager: MeetingSessionManager,
    ) -> None:
        self._acs_client = acs_client
        self._meeting_manager = meeting_manager
        self._active_sessions: dict[str, VoiceSession] = {}
        self._active_workers: dict[str, MeetingAudioWorker] = {}

    # ── WebSocket Handler ────────────────────────────────────────────

    async def handle_websocket(self, request: Request) -> WebSocketResponse:
        """
        Accept a WebSocket upgrade and begin processing audio.

        The ACS media streaming platform connects here after a call is
        answered with media streaming configuration.

        Args:
            request: The incoming aiohttp request to upgrade.

        Returns:
            The WebSocketResponse (kept open for the call duration).
        """
        ws = WebSocketResponse()
        await ws.prepare(request)

        session_id = str(uuid.uuid4())
        logger.info("WebSocket connected: session_id=%s", session_id)

        # Create a VoiceSession to track shared state
        session = VoiceSession(
            session_id=session_id,
            acs_ws=ws,
        )
        self._active_sessions[session_id] = session

        # Create and start the audio worker
        worker = MeetingAudioWorker(
            session=session,
            acs_client=self._acs_client,
            meeting_manager=self._meeting_manager,
        )
        self._active_workers[session_id] = worker

        try:
            # Start the bidirectional audio bridge
            # The worker handles:
            #   1. Connecting to the Realtime API
            #   2. Reading audio from ACS WS and forwarding to Realtime API
            #   3. Reading responses from Realtime API and forwarding to ACS WS
            await worker.start()

            # TODO: Parse initial ACS metadata message to extract
            #       call_connection_id, server_call_id, and participant info
            # TODO: Detect meeting mode vs direct call mode from metadata
            # TODO: Activate wake word detection for meeting mode

            # Keep the WebSocket alive — audio is proxied by the worker
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await worker.handle_acs_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await worker.handle_acs_audio(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(
                        "WebSocket error: session_id=%s, error=%s",
                        session_id,
                        ws.exception(),
                    )
                    break

        except Exception:
            logger.exception("Voice session error: session_id=%s", session_id)
        finally:
            # Clean up
            await worker.stop()
            self._active_sessions.pop(session_id, None)
            self._active_workers.pop(session_id, None)
            logger.info("WebSocket disconnected: session_id=%s", session_id)

        return ws

    # ── Session Access ───────────────────────────────────────────────

    def get_session(self, session_id: str) -> VoiceSession | None:
        """Retrieve an active voice session by ID."""
        return self._active_sessions.get(session_id)

    def get_worker(self, session_id: str) -> MeetingAudioWorker | None:
        """Retrieve an active audio worker by session ID."""
        return self._active_workers.get(session_id)

    def get_session_by_call_connection(self, call_connection_id: str) -> VoiceSession | None:
        """Look up a session by its ACS call connection ID."""
        for session in self._active_sessions.values():
            if session.call_connection_id == call_connection_id:
                return session
        return None

    @property
    def active_session_count(self) -> int:
        """Number of currently active voice sessions."""
        return len(self._active_sessions)

    # ── Shutdown ─────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Gracefully stop all active workers and close sessions."""
        logger.info("Shutting down %d active voice sessions...", len(self._active_workers))
        tasks = [worker.stop() for worker in self._active_workers.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active_sessions.clear()
        self._active_workers.clear()
        logger.info("Voice gateway shutdown complete")
