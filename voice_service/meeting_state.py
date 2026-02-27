"""
voice_service.meeting_state — Meeting session lifecycle management.

Tracks active meeting sessions, coordinates state transitions, and
triggers post-processing (summarisation, notes generation) through
the intelligence service when a meeting ends.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    """Meeting session lifecycle states."""

    CREATED = "created"
    CONNECTED = "connected"
    RECORDING = "recording"
    ENDED = "ended"
    POST_PROCESSING = "post_processing"
    COMPLETED = "completed"


class MeetingSessionManager:
    """
    Manages the lifecycle of meeting sessions.

    Responsibilities:
      - Create and track sessions with their current state.
      - Coordinate state transitions.
      - On meeting end, trigger post-processing via the intelligence
        service API (transcription summary, meeting notes, action items).
      - Persist session metadata via the data gateway.
    """

    def __init__(
        self,
        data_service_url: str = "http://localhost:8081",
        intelligence_service_url: str = "http://localhost:8082",
    ) -> None:
        self._data_service_url = data_service_url.rstrip("/")
        self._intelligence_service_url = intelligence_service_url.rstrip("/")
        self._sessions: dict[str, dict[str, Any]] = {}
        self._http_session: aiohttp.ClientSession | None = None

    async def _get_http_session(self) -> aiohttp.ClientSession:
        """Lazy-init a shared HTTP session for service-to-service calls."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    # ── Session Lifecycle ────────────────────────────────────────────

    async def create_session(
        self,
        meeting_id: str,
        call_connection_id: str,
    ) -> dict[str, Any]:
        """
        Create a new meeting session.

        Args:
            meeting_id: Unique meeting identifier (from Teams or generated).
            call_connection_id: ACS call connection ID for this meeting.

        Returns:
            The newly created session dict.
        """
        session = {
            "meeting_id": meeting_id,
            "call_connection_id": call_connection_id,
            "state": SessionState.CREATED.value,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "participants": [],
            "metadata": {},
        }
        self._sessions[meeting_id] = session

        # TODO: Persist to data service:
        #   POST {data_service_url}/api/meetings
        #   Body: session

        logger.info("Meeting session created: meeting_id=%s, call=%s", meeting_id, call_connection_id)
        return session

    async def get_session(self, meeting_id: str) -> dict[str, Any] | None:
        """
        Retrieve a meeting session by ID.

        Checks the in-memory cache first, then falls back to the data
        service.

        Args:
            meeting_id: The meeting identifier.

        Returns:
            Session dict, or None if not found.
        """
        # Check local cache
        if meeting_id in self._sessions:
            return self._sessions[meeting_id]

        # TODO: Fall back to data service:
        #   GET {data_service_url}/api/meetings/{meeting_id}

        logger.debug("Meeting session not found: meeting_id=%s", meeting_id)
        return None

    async def update_state(
        self,
        meeting_id: str,
        state: SessionState | str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Update the state of a meeting session.

        Args:
            meeting_id: The meeting identifier.
            state: New state (SessionState enum or string value).
            metadata: Optional additional metadata to merge.

        Returns:
            Updated session dict, or None if not found.
        """
        session = self._sessions.get(meeting_id)
        if session is None:
            logger.warning("Cannot update unknown session: meeting_id=%s", meeting_id)
            return None

        if isinstance(state, SessionState):
            state = state.value

        session["state"] = state
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        if metadata:
            session["metadata"].update(metadata)

        # TODO: Persist state change to data service:
        #   PATCH {data_service_url}/api/meetings/{meeting_id}
        #   Body: { "state": state, "metadata": metadata }

        logger.info("Meeting session updated: meeting_id=%s, state=%s", meeting_id, state)
        return session

    async def end_session(self, meeting_id: str) -> None:
        """
        End a meeting session and trigger post-processing.

        This method:
          1. Marks the session as ENDED.
          2. Waits briefly for final transcript persistence (race condition fix).
          3. Triggers the intelligence service to begin post-processing
             (summarisation, meeting notes generation, action item extraction).
          4. Marks the session as POST_PROCESSING.

        NOTE: CallDisconnected fires BEFORE the audio worker finishes
        persisting the final transcript.  The 5-second delay is intentional.

        Args:
            meeting_id: The meeting identifier.
        """
        await self.update_state(meeting_id, SessionState.ENDED)

        # Wait for final transcript persistence (race condition fix)
        # See MEMORY.md: "CallDisconnected fires BEFORE audio worker persists"
        import asyncio
        await asyncio.sleep(5)

        await self.update_state(meeting_id, SessionState.POST_PROCESSING)

        # Trigger intelligence service post-processing
        await self._trigger_post_processing(meeting_id)

    async def _trigger_post_processing(self, meeting_id: str) -> None:
        """
        Call the intelligence service to begin meeting post-processing.

        The intelligence service will:
          - Retrieve the full transcript from the data service
          - Run chunked summarisation via GPT-4o
          - Generate meeting notes (Word doc)
          - Upload to SharePoint
          - Post an Adaptive Card to Teams

        Args:
            meeting_id: The meeting identifier.
        """
        url = f"{self._intelligence_service_url}/api/meetings/{meeting_id}/process"

        try:
            http = await self._get_http_session()
            async with http.post(url, json={"meeting_id": meeting_id}) as resp:
                if resp.status == 200 or resp.status == 202:
                    logger.info("Post-processing triggered: meeting_id=%s", meeting_id)
                    await self.update_state(meeting_id, SessionState.POST_PROCESSING)
                else:
                    body = await resp.text()
                    logger.error(
                        "Post-processing trigger failed: meeting_id=%s, status=%d, body=%s",
                        meeting_id,
                        resp.status,
                        body[:200],
                    )
        except Exception:
            logger.exception("Failed to trigger post-processing: meeting_id=%s", meeting_id)

    # ── Cleanup ──────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
