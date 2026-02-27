"""
voice_service.voice_state — VoiceSession dataclass for per-call state.

Each active call or meeting gets a VoiceSession that tracks participants,
speaker mapping, transcript entries, WebSocket handles, and mode flags.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp
from aiohttp.web import WebSocketResponse


@dataclass
class VoiceSession:
    """
    Runtime state for a single active voice session.

    Created when a WebSocket connection is established and discarded when
    the call ends.  Shared between the VoiceGateway, MeetingAudioWorker,
    and tool-execution layer.
    """

    # ── Identity ─────────────────────────────────────────────────────
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    call_connection_id: str = ""
    server_call_id: str = ""
    meeting_id: str = ""

    # ── Participants ─────────────────────────────────────────────────
    participants: list[str] = field(default_factory=list)
    speaker_map: dict[str, str] = field(default_factory=dict)
    """Maps ACS participantRawId -> display name."""

    # ── Mode flags ───────────────────────────────────────────────────
    is_meeting_mode: bool = False
    """True when AIDA joined a multi-party Teams meeting (vs. direct call)."""
    is_voice_active: bool = False
    """True when AIDA is actively listening/responding (wake word activated)."""

    # ── WebSocket handles ────────────────────────────────────────────
    realtime_ws: aiohttp.ClientWebSocketResponse | None = field(default=None, repr=False)
    """WebSocket connection to the OpenAI Realtime API."""
    acs_ws: WebSocketResponse | None = field(default=None, repr=False)
    """WebSocket connection from the ACS media streaming platform."""

    # ── Transcript ───────────────────────────────────────────────────
    transcript_entries: list[dict[str, str]] = field(default_factory=list)
    """List of {"speaker": ..., "text": ..., "timestamp": ...} dicts."""

    # ── Timing ───────────────────────────────────────────────────────
    start_time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ── Methods ──────────────────────────────────────────────────────

    def get_speaker_name(self, participant_raw_id: str) -> str:
        """
        Resolve a participantRawId to a display name.

        Falls back to a truncated raw ID if no mapping is found.

        Args:
            participant_raw_id: ACS participant raw identifier string.

        Returns:
            Human-readable speaker name.
        """
        if participant_raw_id in self.speaker_map:
            return self.speaker_map[participant_raw_id]
        # Fallback: use last 8 chars of the raw ID
        return f"Speaker-{participant_raw_id[-8:]}" if participant_raw_id else "Unknown"

    def add_transcript_entry(self, speaker: str, text: str) -> None:
        """
        Append a new transcript entry with an automatic timestamp.

        Args:
            speaker: Display name of the speaker.
            text: The spoken text (transcription result).
        """
        self.transcript_entries.append({
            "speaker": speaker,
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def to_dict(self) -> dict[str, Any]:
        """
        Serialise the session state to a plain dict.

        Suitable for JSON serialisation, Redis storage, or API responses.
        Excludes WebSocket handles (not serialisable).

        Returns:
            Dictionary representation of the session.
        """
        return {
            "session_id": self.session_id,
            "call_connection_id": self.call_connection_id,
            "server_call_id": self.server_call_id,
            "meeting_id": self.meeting_id,
            "participants": self.participants,
            "speaker_map": self.speaker_map,
            "is_meeting_mode": self.is_meeting_mode,
            "is_voice_active": self.is_voice_active,
            "transcript_entries": self.transcript_entries,
            "start_time": self.start_time,
        }
