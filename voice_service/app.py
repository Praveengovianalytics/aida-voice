"""
voice_service.app — aiohttp web application and server bootstrap.

Configures the aiohttp web.Application with routes for:
  - WS  /voice-v2             — Voice gateway WebSocket (client audio bridge)
  - POST /api/calls/webhook   — ACS call lifecycle event handler
  - POST /api/calls/incoming  — Teams incoming call notification handler
  - POST /api/calls/create    — Create outbound call endpoint
  - GET  /health              — Health check endpoint

Initialises the ACS client and data gateway client on startup, then
starts the server on port 3979.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from aiohttp import web
from aiohttp.web import Request, Response

from aida_sdk.clients.acs_client import ACSClient
from aida_sdk.config import settings

from voice_service.voice_gateway import VoiceGateway
from voice_service.webhooks.acs_webhook import handle_acs_event
from voice_service.webhooks.calling_webhook import handle_incoming_call
from voice_service.meeting_state import MeetingSessionManager

logger = logging.getLogger(__name__)

PORT = int(os.getenv("PORT", "3979"))


# ---------------------------------------------------------------------------
# Application state (populated at startup)
# ---------------------------------------------------------------------------
_acs_client: ACSClient | None = None
_meeting_manager: MeetingSessionManager | None = None
_voice_gateway: VoiceGateway | None = None


def get_acs_client() -> ACSClient:
    """Return the singleton ACS client. Raises if called before startup."""
    assert _acs_client is not None, "ACS client not initialised"
    return _acs_client


def get_meeting_manager() -> MeetingSessionManager:
    """Return the singleton meeting session manager."""
    assert _meeting_manager is not None, "Meeting session manager not initialised"
    return _meeting_manager


def get_voice_gateway() -> VoiceGateway:
    """Return the singleton voice gateway."""
    assert _voice_gateway is not None, "Voice gateway not initialised"
    return _voice_gateway


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------
async def on_startup(app: web.Application) -> None:
    """Initialise shared clients and services."""
    global _acs_client, _meeting_manager, _voice_gateway

    logger.info("Initialising ACS client...")
    _acs_client = ACSClient()

    logger.info("Initialising meeting session manager...")
    _meeting_manager = MeetingSessionManager(
        data_service_url=settings.DATA_SERVICE_URL,
        intelligence_service_url=settings.INTELLIGENCE_SERVICE_URL,
    )

    logger.info("Initialising voice gateway...")
    _voice_gateway = VoiceGateway(
        acs_client=_acs_client,
        meeting_manager=_meeting_manager,
    )

    # Stash references on the app dict so handlers can access them
    app["acs_client"] = _acs_client
    app["meeting_manager"] = _meeting_manager
    app["voice_gateway"] = _voice_gateway

    logger.info("Voice service startup complete")


async def on_shutdown(app: web.Application) -> None:
    """Graceful shutdown — close active sessions and clients."""
    gateway: VoiceGateway | None = app.get("voice_gateway")
    if gateway:
        await gateway.shutdown()
    logger.info("Voice service shutdown complete")


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------
async def health(request: Request) -> Response:
    """Health check endpoint for container orchestrators."""
    return web.json_response({"status": "healthy", "service": "aida-voice"})


async def create_outbound_call(request: Request) -> Response:
    """
    Create an outbound call.

    Expects JSON body:
    {
        "target": "+15551234567" | "8:acs:...",
        "meeting_id": "optional-meeting-id",
        "media_streaming": true | false
    }
    """
    body: dict[str, Any] = await request.json()
    target = body.get("target", "")
    meeting_id = body.get("meeting_id", "")

    if not target:
        return web.json_response({"error": "target is required"}, status=400)

    acs = get_acs_client()
    callback_uri = f"{settings.BOT_CALLBACK_HOST}/api/calls/webhook"

    # Configure media streaming if requested
    media_config: dict[str, Any] | None = None
    if body.get("media_streaming", True):
        ws_host = settings.BOT_CALLBACK_HOST.replace("https://", "wss://").replace("http://", "ws://")
        media_config = {
            "transport_url": f"{ws_host}/voice-v2",
        }

    try:
        result = await acs.create_call(
            target=target,
            callback_uri=callback_uri,
            media_config=media_config,
        )
        connection_id = result.call_connection.call_connection_id

        # Register with meeting manager if meeting_id provided
        if meeting_id:
            manager = get_meeting_manager()
            await manager.create_session(meeting_id, connection_id)

        return web.json_response({
            "call_connection_id": connection_id,
            "meeting_id": meeting_id or connection_id,
        })
    except Exception:
        logger.exception("Failed to create outbound call to %s", target)
        return web.json_response({"error": "Failed to create call"}, status=500)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app() -> web.Application:
    """Build and return the configured aiohttp Application."""
    app = web.Application()

    # ── CORS middleware ──────────────────────────────────────────────
    @web.middleware
    async def cors_middleware(request: Request, handler):
        if request.method == "OPTIONS":
            response = Response(status=200)
        else:
            response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response

    app.middlewares.append(cors_middleware)

    # ── Lifecycle hooks ──────────────────────────────────────────────
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # ── Voice gateway (WebSocket) ────────────────────────────────────
    gateway_handler = lambda request: get_voice_gateway().handle_websocket(request)  # noqa: E731
    app.router.add_get("/voice-v2", gateway_handler)

    # ── ACS webhooks ─────────────────────────────────────────────────
    app.router.add_post("/api/calls/webhook", handle_acs_event)
    app.router.add_post("/api/calls/incoming", handle_incoming_call)

    # ── Outbound call creation ───────────────────────────────────────
    app.router.add_post("/api/calls/create", create_outbound_call)

    # ── Health ───────────────────────────────────────────────────────
    app.router.add_get("/health", health)

    return app


def main() -> None:
    """Start the aiohttp server on the configured port."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    app = create_app()
    logger.info("Starting aida-voice on port %d", PORT)
    web.run_app(app, host="0.0.0.0", port=PORT)
