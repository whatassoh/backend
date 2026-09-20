from collections.abc import Callable
from dataclasses import replace

from django.db import transaction
from django.utils import timezone

from apps.conversations.domain.context import (
    ConversationContext,
    ConversationFlow,
    PendingClarification,
)
from apps.conversations.domain.enums import ConversationStatus
from apps.conversations.domain.exceptions import ConversationAlreadyClosedError
from apps.conversations.infrastructure.models import Conversation


class ConversationContextService:
    """The sole typed read/write boundary for temporary conversation JSON."""

    processed_message_limit = 20

    def get_context(self, conversation: Conversation) -> ConversationContext:
        if conversation.status == ConversationStatus.CLOSED:
            return ConversationContext()
        return ConversationContext.from_payload(conversation.context)

    @transaction.atomic
    def update_context(
        self, conversation: Conversation, context: ConversationContext
    ) -> Conversation:
        return self._mutate(conversation, lambda _: context)

    @transaction.atomic
    def _mutate(
        self,
        conversation: Conversation,
        transform: Callable[[ConversationContext], ConversationContext],
    ) -> Conversation:
        locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
        if locked.status == ConversationStatus.CLOSED:
            raise ConversationAlreadyClosedError("Cannot update a closed conversation.")
        locked.context = transform(
            ConversationContext.from_payload(locked.context)
        ).to_payload()
        locked.save(update_fields=["context", "updated_at"])
        return locked

    def clear_context(self, conversation: Conversation) -> Conversation:
        return self.update_context(conversation, ConversationContext())

    def set_flow(
        self, conversation: Conversation, flow: ConversationFlow
    ) -> Conversation:
        return self._mutate(conversation, lambda context: replace(context, flow=flow))

    def set_active_journey(self, conversation: Conversation, journey) -> Conversation:
        return self._mutate(
            conversation, lambda context: replace(context, active_journey=journey)
        )

    def set_pending_clarification(
        self,
        conversation: Conversation,
        pending: PendingClarification,
        *,
        flow: ConversationFlow = ConversationFlow.AWAITING_CLARIFICATION,
    ) -> Conversation:
        pending = replace(
            pending, created_at=pending.created_at or timezone.now().isoformat()
        )
        return self._mutate(
            conversation,
            lambda context: replace(context, flow=flow, pending_clarification=pending),
        )

    def clear_pending_clarification(
        self,
        conversation: Conversation,
        *,
        flow: ConversationFlow = ConversationFlow.IDLE,
    ) -> Conversation:
        return self._mutate(
            conversation,
            lambda context: replace(context, flow=flow, pending_clarification=None),
        )

    def mark_message_processed(
        self, conversation: Conversation, message_id: str
    ) -> bool:
        processed = False

        def add_message(context: ConversationContext) -> ConversationContext:
            nonlocal processed
            if message_id in context.processed_message_ids:
                return context
            processed = True
            message_ids = (*context.processed_message_ids, message_id)[
                -self.processed_message_limit :
            ]
            return replace(context, processed_message_ids=message_ids)

        self._mutate(conversation, add_message)
        return processed
