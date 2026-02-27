"""
voice_service.webhooks.calling_webhook — Incoming call notification handler.

Handles the Teams/ACS incoming call event, answers the call with
media streaming configuration, and creates a MeetingAudioWorker to
begin processing audio.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from aiohttp.web import Request, Response, json_response

from aida_sdk.config import settings

logger = logging.getLogger(__name__)


async def handle_incoming_call(request: Request) -> Response:
    """
    Handle an incoming call notification from ACS / Teams.

    When a call comes in (either PSTN, VoIP, or Teams interop), ACS
    sends an IncomingCall event.  This handler:
      1. Extracts the incoming call context.
      2. Determines call mode (meeting join vs. direct call).
      3. Answers the call with media streaming configuration.
      4. Creates a meeting session (if applicable).

    Args:
        request: The incoming aiohttp request with call notification.

    Returns:
        200 OK with call details, or error response.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in incoming call webhook")
        return json_response({"error": "Invalid JSON"}, status=400)

    # Handle as CloudEvents array or single event
    events: list[dict[str, Any]] = body if isinstance(body, list) else [body]

    for event in events:
        event_type = event.get("type", "")
        event_data = event.get("data", {})

        # ── Event Grid Validation ────────────────────────────────────
        if event_type == "Microsoft.EventGrid.SubscriptionValidationEvent":
            validation_code = event_data.get("validationCode", "")
            return json_response({"validationResponse": validation_code})

        # ── Incoming Call ────────────────────────────────────────────
        if event_type == "Microsoft.Communication.IncomingCall":
            return await _handle_incoming(request, event_data)

    return json_response({"status": "ok"})


async def _handle_incoming(request: Request, data: dict[str, Any]) -> Response:
    """
    Process an IncomingCall event — answer with media streaming.

    Args:
        request: The aiohttp request (for accessing app-level services).
        data: The IncomingCall event data payload.

    Returns:
        JSON response with call connection details.
    """
    incoming_call_context = data.get("incomingCallContext", "")
    caller_raw_id = data.get("from", {}).get("rawId", "")
    caller_display_name = data.get("from", {}).get("displayName", "Unknown Caller")
    to_raw_id = data.get("to", {}).get("rawId", "")

    # Determine if this is a meeting join or a direct call
    # meeting_join_url sentinels: "direct-call", "incoming-call"
    meeting_join_url = data.get("customContext", {}).get("meetingJoinUrl", "")
    is_meeting = bool(meeting_join_url) and meeting_join_url not in ("direct-call", "incoming-call")

    meeting_id = str(uuid.uuid4())
    call_mode = "meeting" if is_meeting else "direct-call"

    logger.info(
        "Incoming call: from=%s (%s), mode=%s, meeting_id=%s",
        caller_display_name,
        caller_raw_id,
        call_mode,
        meeting_id,
    )

    if not incoming_call_context:
        logger.error("Missing incomingCallContext — cannot answer call")
        return json_response({"error": "Missing incomingCallContext"}, status=400)

    # Build callback URI for subsequent events
    callback_uri = f"{settings.BOT_CALLBACK_HOST}/api/calls/webhook"

    # Configure media streaming — ACS will connect a WebSocket to /voice-v2
    ws_host = settings.BOT_CALLBACK_HOST.replace("https://", "wss://").replace("http://", "ws://")
    media_config = {
        "transport_url": f"{ws_host}/voice-v2",
    }

    # Answer the call via ACS
    acs_client = request.app.get("acs_client")
    if not acs_client:
        logger.error("ACS client not available — cannot answer call")
        return json_response({"error": "Service not ready"}, status=503)

    try:
        result = await acs_client.answer_call(
            incoming_call_context=incoming_call_context,
            callback_uri=callback_uri,
            media_config=media_config,
        )
        call_connection_id = result.call_connection.call_connection_id
    except Exception:
        logger.exception("Failed to answer incoming call")
        return json_response({"error": "Failed to answer call"}, status=500)

    # Create meeting session
    meeting_manager = request.app.get("meeting_manager")
    if meeting_manager:
        await meeting_manager.create_session(meeting_id, call_connection_id)

    # TODO: Look up caller in org directory for personalised greeting
    # TODO: Set is_meeting_mode on the VoiceSession once the WebSocket connects
    # TODO: Pre-load caller's recent meetings/action items for context

    logger.info(
        "Call answered: call_connection_id=%s, meeting_id=%s, mode=%s",
        call_connection_id,
        meeting_id,
        call_mode,
    )

    return json_response({
        "call_connection_id": call_connection_id,
        "meeting_id": meeting_id,
        "mode": call_mode,
        "caller": caller_display_name,
    })
