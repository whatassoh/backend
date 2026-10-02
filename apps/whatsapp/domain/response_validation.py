"""Channel constraints for conversational response plans.

The conversation layer deliberately stays channel-neutral.  This module is the
single place where a plan is checked and where its WhatsApp interactive
representation is selected before it can enter the durable outbox.
"""

from enum import StrEnum

from apps.messaging.domain.enums import MessagingChannel
from apps.whatsapp.exceptions import WhatsAppInvalidPayloadError


class WhatsAppInteractiveMode(StrEnum):
    REPLY_BUTTONS = "reply_buttons"
    LIST = "list"


def validate_response_for_channel(
    response, channel: str
) -> WhatsAppInteractiveMode | None:
    """Validate a channel-neutral response without changing its actions.

    WhatsApp reply buttons have a hard three-button limit.  Menus of four to
    ten actions are sent explicitly as a WhatsApp list, whose replies are
    already normalized by the inbound adapter.  Larger menus are rejected
    rather than silently losing an action.
    """
    if channel != MessagingChannel.WHATSAPP:
        return None

    actions = tuple(getattr(response, "actions", ()) or ())
    if not actions:
        return None
    for action in actions:
        action_id = getattr(action, "action_id", None)
        label = getattr(action, "label", None)
        if not isinstance(action_id, str) or not action_id.strip():
            raise WhatsAppInvalidPayloadError(
                "WhatsApp response actions require a non-empty action_id."
            )
        if not isinstance(label, str) or not label.strip():
            raise WhatsAppInvalidPayloadError(
                "WhatsApp response actions require a non-empty label."
            )

    if len(actions) <= 3:
        return WhatsAppInteractiveMode.REPLY_BUTTONS
    if len(actions) <= 10:
        return WhatsAppInteractiveMode.LIST
    raise WhatsAppInvalidPayloadError(
        "WhatsApp interactive lists support at most 10 actions."
    )
