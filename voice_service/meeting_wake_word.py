"""
voice_service.meeting_wake_word — Wake word detection for meeting mode.

In meeting mode AIDA listens passively to the conversation and only
activates (starts responding via the Realtime API) when addressed
directly.  This module provides simple text-based wake word detection.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_service.voice_state import VoiceSession

logger = logging.getLogger(__name__)

# Wake word patterns — case-insensitive
# "Hey AIDA", "AIDA", "Hey Ada", "Ada" (common mis-transcriptions)
_WAKE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bhey\s+aida\b", re.IGNORECASE),
    re.compile(r"\baida\b", re.IGNORECASE),
    re.compile(r"\bhey\s+ada\b", re.IGNORECASE),
]

# Deactivation patterns
_DEACTIVATE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bthanks?\s+aida\b", re.IGNORECASE),
    re.compile(r"\bthat'?s?\s+all\s+aida\b", re.IGNORECASE),
    re.compile(r"\bnever\s*mind\b", re.IGNORECASE),
]


class WakeWordDetector:
    """
    Detects wake words in transcribed speech to activate/deactivate AIDA.

    In meeting mode, AIDA stays passive until someone says "Hey AIDA"
    or similar.  After responding, AIDA can be deactivated with phrases
    like "Thanks AIDA" or "That's all AIDA".

    TODO: Add Voice Activity Detection (VAD) for more sophisticated
          detection — energy-based filtering before running text matching.
    TODO: Consider a small on-device wake word model (e.g., Porcupine)
          for lower latency detection before transcription completes.
    TODO: Add configurable wake word timeout — auto-deactivate after
          N seconds of silence following the last AIDA interaction.
    """

    def __init__(self, auto_deactivate_seconds: float = 30.0) -> None:
        self._auto_deactivate_seconds = auto_deactivate_seconds

    def check_transcript(self, text: str) -> bool:
        """
        Check whether the transcribed text contains a wake word.

        Args:
            text: Transcribed speech text to check.

        Returns:
            True if a wake word was detected, False otherwise.
        """
        for pattern in _WAKE_PATTERNS:
            if pattern.search(text):
                logger.info("Wake word detected in: %s", text[:80])
                return True
        return False

    def check_deactivate(self, text: str) -> bool:
        """
        Check whether the transcribed text contains a deactivation phrase.

        Args:
            text: Transcribed speech text to check.

        Returns:
            True if a deactivation phrase was detected, False otherwise.
        """
        for pattern in _DEACTIVATE_PATTERNS:
            if pattern.search(text):
                logger.info("Deactivation phrase detected in: %s", text[:80])
                return True
        return False

    def activate(self, session: "VoiceSession") -> None:
        """
        Activate voice mode for the session.

        Sets ``is_voice_active = True`` so the audio worker begins
        forwarding audio to the Realtime API.

        Args:
            session: The VoiceSession to activate.
        """
        if not session.is_voice_active:
            session.is_voice_active = True
            logger.info("Voice activated: session=%s", session.session_id)

    def deactivate(self, session: "VoiceSession") -> None:
        """
        Deactivate voice mode for the session.

        Sets ``is_voice_active = False`` so the audio worker stops
        forwarding audio to the Realtime API (but continues collecting
        transcript for meeting notes).

        Args:
            session: The VoiceSession to deactivate.
        """
        if session.is_voice_active:
            session.is_voice_active = False
            logger.info("Voice deactivated: session=%s", session.session_id)
