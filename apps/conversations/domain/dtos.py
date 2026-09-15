from dataclasses import dataclass

from apps.conversations.infrastructure.models import Conversation, ConversationMessage
from apps.messaging.infrastructure.models import ChannelIdentity


@dataclass(frozen=True, kw_only=True)
class InboundMessageContext:
    user: object
    channel_identity: ChannelIdentity
    conversation: Conversation
    conversation_message: ConversationMessage
    created_conversation: bool = False
    created_message: bool = False
