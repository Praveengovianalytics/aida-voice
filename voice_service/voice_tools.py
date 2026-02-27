"""
voice_service.voice_tools — Tool definitions and dispatcher for the Realtime API.

Defines the VOICE_TOOLS list (OpenAI function-calling schema) and the
``execute_tool()`` dispatcher that routes tool calls to the appropriate
handler.  Each tool typically calls an aida-sdk client or the data/
intelligence service API.
"""

from __future__ import annotations

import logging
from typing import Any

from voice_service.voice_state import VoiceSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------

VOICE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_call_context",
        "description": (
            "Get information about the current call — who is on the call, "
            "how long it has been going, meeting subject, and participants."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "search_knowledge",
        "description": (
            "Search the organisation's knowledge base (documents, wikis, "
            "policies) for relevant information. Use this when the caller "
            "asks a factual question about the organisation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query — be specific and include key terms.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "get_meeting_notes",
        "description": (
            "Retrieve past meeting notes. Can search by meeting subject, "
            "date range, or participants."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — meeting subject, keywords, or participant name.",
                },
                "days_back": {
                    "type": "integer",
                    "description": "Number of days to look back (default 30).",
                    "default": 30,
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "get_calendar",
        "description": (
            "Get the user's calendar events for a given time range. "
            "Defaults to today if no range is specified."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "time_range": {
                    "type": "string",
                    "description": (
                        "Natural language time range, e.g. 'today', 'tomorrow', "
                        "'next week', 'this afternoon'."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "send_email_draft",
        "description": (
            "Draft and send an email on behalf of the user. The email is "
            "sent as a draft that the user can review and send."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address or name (will be resolved).",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line.",
                },
                "body": {
                    "type": "string",
                    "description": "Email body text.",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "type": "function",
        "name": "schedule_meeting",
        "description": (
            "Schedule a new meeting on the user's calendar. Creates the "
            "event and sends invitations to attendees."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Meeting subject/title.",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses or names.",
                },
                "time": {
                    "type": "string",
                    "description": (
                        "Natural language time, e.g. 'tomorrow at 2pm', "
                        "'next Monday at 10am'."
                    ),
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Meeting duration in minutes (default 30).",
                    "default": 30,
                },
            },
            "required": ["subject", "attendees", "time"],
        },
    },
    {
        "type": "function",
        "name": "web_search",
        "description": (
            "Search the web for up-to-date information. Use this when the "
            "caller asks about current events, public information, or "
            "anything not in the knowledge base."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The web search query.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "get_action_status",
        "description": (
            "Check the status of a previously created action item or task. "
            "Can look up by description or assignee."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Action item description, keyword, or assignee name.",
                },
            },
            "required": ["query"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

async def execute_tool(
    tool_name: str,
    args: dict[str, Any],
    session: VoiceSession,
) -> dict[str, Any]:
    """
    Execute a voice tool and return the result.

    Each tool handler is responsible for calling the appropriate service
    (via aida-sdk clients or the data/intelligence service HTTP API)
    and returning a dict that will be serialised to JSON and sent back
    to the Realtime API as a function_call_output.

    Args:
        tool_name: Name of the tool to execute.
        args: Parsed arguments from the Realtime API.
        session: The active VoiceSession (for call context).

    Returns:
        Result dict to be sent back to the Realtime API.
    """
    handler = _TOOL_HANDLERS.get(tool_name)
    if not handler:
        logger.warning("Unknown tool: %s", tool_name)
        return {"error": f"Unknown tool: {tool_name}"}

    logger.info("Executing tool: %s (args=%s)", tool_name, args)
    return await handler(args, session)


# ---------------------------------------------------------------------------
# Individual tool handlers
# ---------------------------------------------------------------------------

async def _get_call_context(args: dict[str, Any], session: VoiceSession) -> dict[str, Any]:
    """Return information about the current call."""
    return {
        "session_id": session.session_id,
        "call_connection_id": session.call_connection_id,
        "meeting_id": session.meeting_id,
        "is_meeting_mode": session.is_meeting_mode,
        "participants": session.participants,
        "speaker_map": session.speaker_map,
        "start_time": session.start_time,
        "transcript_count": len(session.transcript_entries),
    }


async def _search_knowledge(args: dict[str, Any], session: VoiceSession) -> dict[str, Any]:
    """Search the organisation's knowledge base."""
    query = args.get("query", "")

    # TODO: Use aida_sdk SearchClient to query Azure AI Search
    #   from aida_sdk.clients import SearchClient
    #   client = SearchClient()
    #   results = await client.search(query)
    #   return {"results": results}

    logger.info("Knowledge search: query=%s", query)
    return {"results": [], "message": "TODO: Implement knowledge search via aida-sdk SearchClient"}


async def _get_meeting_notes(args: dict[str, Any], session: VoiceSession) -> dict[str, Any]:
    """Retrieve past meeting notes from the data service."""
    query = args.get("query", "")
    days_back = args.get("days_back", 30)

    # TODO: Query aida-data service:
    #   GET {DATA_SERVICE_URL}/api/meeting-notes?query={query}&days_back={days_back}

    logger.info("Meeting notes search: query=%s, days_back=%d", query, days_back)
    return {"notes": [], "message": "TODO: Implement meeting notes retrieval via data service"}


async def _get_calendar(args: dict[str, Any], session: VoiceSession) -> dict[str, Any]:
    """Get calendar events for the specified time range."""
    time_range = args.get("time_range", "today")

    # TODO: Use aida_sdk GraphClient to query calendar:
    #   from aida_sdk.clients import GraphClient
    #   client = GraphClient()
    #   events = await client.get_calendar_events(time_range)
    #   return {"events": events}

    logger.info("Calendar query: time_range=%s", time_range)
    return {"events": [], "message": "TODO: Implement calendar retrieval via GraphClient"}


async def _send_email_draft(args: dict[str, Any], session: VoiceSession) -> dict[str, Any]:
    """Draft and send an email."""
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")

    # TODO: Use aida_sdk GraphClient to create draft email:
    #   from aida_sdk.clients import GraphClient
    #   client = GraphClient()
    #   result = await client.create_draft_email(to, subject, body)
    #   return {"status": "draft_created", "message_id": result["id"]}

    logger.info("Email draft: to=%s, subject=%s", to, subject)
    return {"status": "draft_created", "message": "TODO: Implement email draft via GraphClient"}


async def _schedule_meeting(args: dict[str, Any], session: VoiceSession) -> dict[str, Any]:
    """Schedule a new calendar meeting."""
    subject = args.get("subject", "")
    attendees = args.get("attendees", [])
    time = args.get("time", "")
    duration_minutes = args.get("duration_minutes", 30)

    # TODO: Use aida_sdk GraphClient to create calendar event:
    #   from aida_sdk.clients import GraphClient
    #   client = GraphClient()
    #   result = await client.create_event(subject, attendees, time, duration_minutes)
    #   return {"status": "scheduled", "event_id": result["id"]}

    logger.info("Schedule meeting: subject=%s, attendees=%s, time=%s", subject, attendees, time)
    return {"status": "scheduled", "message": "TODO: Implement meeting scheduling via GraphClient"}


async def _web_search(args: dict[str, Any], session: VoiceSession) -> dict[str, Any]:
    """Search the web for current information."""
    query = args.get("query", "")

    # TODO: Use aida_sdk WebSearchClient:
    #   from aida_sdk.clients import WebSearchClient
    #   client = WebSearchClient()
    #   results = await client.search(query)
    #   return {"results": results}

    logger.info("Web search: query=%s", query)
    return {"results": [], "message": "TODO: Implement web search via WebSearchClient"}


async def _get_action_status(args: dict[str, Any], session: VoiceSession) -> dict[str, Any]:
    """Check the status of action items."""
    query = args.get("query", "")

    # TODO: Query aida-data service:
    #   GET {DATA_SERVICE_URL}/api/actions?query={query}

    logger.info("Action status query: query=%s", query)
    return {"actions": [], "message": "TODO: Implement action status via data service"}


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_TOOL_HANDLERS = {
    "get_call_context": _get_call_context,
    "search_knowledge": _search_knowledge,
    "get_meeting_notes": _get_meeting_notes,
    "get_calendar": _get_calendar,
    "send_email_draft": _send_email_draft,
    "schedule_meeting": _schedule_meeting,
    "web_search": _web_search,
    "get_action_status": _get_action_status,
}
