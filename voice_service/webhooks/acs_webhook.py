"""
voice_service.webhooks.acs_webhook — ACS call lifecycle event handler.

Receives POST callbacks from Azure Communication Services for call
events: CallConnected, CallDisconnected, PlayCompleted,
RecognizeCompleted, ParticipantsUpdated, MediaStreamingStarted,
MediaStreamingStopped.

Each event is routed to the appropriate handler which updates session
state, manages audio workers, and triggers post-processing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp.web import Request, Response, json_response

logger = logging.getLogger(__name__)


async def handle_acs_event(request: Request) -> Response:
    """
    Handle ACS Call Automation webhook events.

    ACS sends a JSON array of CloudEvent-formatted events to this
    endpoint for each call lifecycle transition.

    Args:
        request: The incoming aiohttp request containing ACS events.

    Returns:
        200 OK to acknowledge receipt.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in ACS webhook request")
        return json_response({"error": "Invalid JSON"}, status=400)

    # ACS sends events as a JSON array (CloudEvents batch)
    events: list[dict[str, Any]] = body if isinstance(body, list) else [body]

    for event in events:
        # CloudEvents envelope
        event_type = event.get("type", "")
        event_data = event.get("data", {})

        # Extract common fields
        call_connection_id = event_data.get("callConnectionId", "")
        server_call_id = event_data.get("serverCallId", "")
        correlation_id = event_data.get("correlationId", "")

        logger.info(
            "ACS event: type=%s, call=%s, server_call=%s",
            event_type,
            call_connection_id,
            server_call_id,
        )

        # ── Event Subscription Validation ────────────────────────────
        if event_type == "Microsoft.EventGrid.SubscriptionValidationEvent":
            validation_code = event_data.get("validationCode", "")
            logger.info("Event Grid validation: code=%s", validation_code)
            return json_response({"validationResponse": validation_code})

        # ── Call Connected ───────────────────────────────────────────
        elif event_type == "Microsoft.Communication.CallConnected":
            await _handle_call_connected(request, event_data, call_connection_id, server_call_id)

        # ── Call Disconnected ────────────────────────────────────────
        elif event_type == "Microsoft.Communication.CallDisconnected":
            await _handle_call_disconnected(request, event_data, call_connection_id)

        # ── Play Completed ───────────────────────────────────────────
        elif event_type == "Microsoft.Communication.PlayCompleted":
            await _handle_play_completed(request, event_data, call_connection_id)

        # ── Recognize Completed ──────────────────────────────────────
        elif event_type == "Microsoft.Communication.RecognizeCompleted":
            await _handle_recognize_completed(request, event_data, call_connection_id)

        # ── Participants Updated ─────────────────────────────────────
        elif event_type == "Microsoft.Communication.ParticipantsUpdated":
            await _handle_participants_updated(request, event_data, call_connection_id)

        # ── Media Streaming Started ──────────────────────────────────
        elif event_type == "Microsoft.Communication.MediaStreamingStarted":
            await _handle_media_streaming_started(request, event_data, call_connection_id)

        # ── Media Streaming Stopped ──────────────────────────────────
        elif event_type == "Microsoft.Communication.MediaStreamingStopped":
            await _handle_media_streaming_stopped(request, event_data, call_connection_id)

        else:
            logger.debug("Unhandled ACS event type: %s", event_type)

    return json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

async def _handle_call_connected(
    request: Request,
    data: dict[str, Any],
    call_connection_id: str,
    server_call_id: str,
) -> None:
    """
    Handle CallConnected — the call is fully established.

    TODO: Look up or create the VoiceSession for this call.
    TODO: Update session with call_connection_id and server_call_id.
    TODO: If meeting mode, start passive listening.
    TODO: If direct call, activate voice immediately.
    """
    logger.info("Call connected: call=%s, server_call=%s", call_connection_id, server_call_id)

    # Access the voice gateway from the app
    gateway = request.app.get("voice_gateway")
    if gateway:
        session = gateway.get_session_by_call_connection(call_connection_id)
        if session:
            session.server_call_id = server_call_id
            logger.info("Session updated with server_call_id: session=%s", session.session_id)


async def _handle_call_disconnected(
    request: Request,
    data: dict[str, Any],
    call_connection_id: str,
) -> None:
    """
    Handle CallDisconnected — the call has ended.

    NOTE: This event fires BEFORE the audio worker finishes persisting
    the final transcript entries.  The MeetingSessionManager.end_session()
    method includes a 5-second delay to handle this race condition.

    TODO: Find the session by call_connection_id and trigger cleanup.
    TODO: Ensure the audio worker's stop() method is called.
    """
    logger.info("Call disconnected: call=%s", call_connection_id)

    gateway = request.app.get("voice_gateway")
    if gateway:
        session = gateway.get_session_by_call_connection(call_connection_id)
        if session:
            worker = gateway.get_worker(session.session_id)
            if worker:
                # Worker stop() will persist transcript and trigger post-processing
                await worker.stop()


async def _handle_play_completed(
    request: Request,
    data: dict[str, Any],
    call_connection_id: str,
) -> None:
    """
    Handle PlayCompleted — an audio prompt finished playing.

    TODO: Implement if using ACS Play for initial greetings or hold music.
    """
    logger.debug("Play completed: call=%s", call_connection_id)


async def _handle_recognize_completed(
    request: Request,
    data: dict[str, Any],
    call_connection_id: str,
) -> None:
    """
    Handle RecognizeCompleted — speech/DTMF recognition finished.

    TODO: Implement if using ACS Recognize for DTMF menus or
          speech-to-text fallback (separate from Realtime API).
    """
    logger.debug("Recognize completed: call=%s", call_connection_id)


async def _handle_participants_updated(
    request: Request,
    data: dict[str, Any],
    call_connection_id: str,
) -> None:
    """
    Handle ParticipantsUpdated — a participant joined or left.

    Updates the session's participant list and speaker map.

    TODO: Extract participant display names and raw IDs.
    TODO: Update session.participants and session.speaker_map.
    TODO: If in meeting mode, update Realtime API instructions with
          current participant names via session.update.
    """
    participants = data.get("participants", [])
    logger.info(
        "Participants updated: call=%s, count=%d",
        call_connection_id,
        len(participants),
    )

    gateway = request.app.get("voice_gateway")
    if gateway:
        session = gateway.get_session_by_call_connection(call_connection_id)
        if session:
            for p in participants:
                raw_id = p.get("rawId", "")
                display_name = p.get("displayName", "")
                if raw_id and display_name:
                    session.speaker_map[raw_id] = display_name
                if display_name and display_name not in session.participants:
                    session.participants.append(display_name)


async def _handle_media_streaming_started(
    request: Request,
    data: dict[str, Any],
    call_connection_id: str,
) -> None:
    """
    Handle MediaStreamingStarted — ACS is now streaming audio.

    The WebSocket connection should already be established by this point.

    TODO: Confirm that the audio worker is running for this call.
    TODO: Log media streaming configuration details.
    """
    logger.info("Media streaming started: call=%s", call_connection_id)


async def _handle_media_streaming_stopped(
    request: Request,
    data: dict[str, Any],
    call_connection_id: str,
) -> None:
    """
    Handle MediaStreamingStopped — ACS stopped streaming audio.

    This typically happens when the call ends or is transferred.

    TODO: Signal the audio worker to stop if still running.
    """
    logger.info("Media streaming stopped: call=%s", call_connection_id)
