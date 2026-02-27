"""
voice_service.webhooks — ACS call event and incoming call webhook handlers.
"""

from voice_service.webhooks.acs_webhook import handle_acs_event
from voice_service.webhooks.calling_webhook import handle_incoming_call

__all__ = ["handle_acs_event", "handle_incoming_call"]
