from types import SimpleNamespace

import pytest
from django.test import override_settings

from apps.ai.application.conversation_memory import IncrementalConversationMemory
from apps.conversations.application.memory import (
    ConversationMemoryService,
    extract_explicit_preferences,
)
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.application.style import observe_writing_style, style_snapshot
from apps.conversations.infrastructure.models import (
    ConversationMessage,
    ConversationSummary,
    MemoryFact,
    UserCommunicationProfile,
)
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessageDirection, MessageType, MessagingChannel


def make_conversation(number):
    user = User.objects.create_user(
        whatsapp_phone=f"+237699{number:06d}", first_name=f"User {number}"
    )
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=f"memory-{number}",
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


class SummaryAI:
    def generate_structured(self, request):
        return SimpleNamespace(
            data={
                "confirmed_facts": [],
                "hypotheses": [],
                "executed_actions": [],
                "active_tasks": ["Création de boutique"],
                "suspended_tasks": [],
                "missing_information": ["Position"],
                "assistant_commitments": [],
                "social_preferences": [],
                "episode": None,
            }
        )


def test_preference_extractor_ignores_business_claims():
    assert extract_explicit_preferences("Ma commande 123 coûte 5000 FCFA") == ()
    facts = extract_explicit_preferences("Tutoie-moi et réponds brièvement, sans emoji")
    assert {(item.fact_type, item.value) for item in facts} == {
        ("address_form", "tu"),
        ("response_length", "short"),
        ("emoji_preference", "none"),
    }


@pytest.mark.django_db
@override_settings(AI_MEMORY_FACT_TTL_DAYS=365)
def test_memory_is_user_scoped_and_forget_is_soft_delete():
    first = make_conversation(1)
    second = make_conversation(2)
    service = ConversationMemoryService()

    service.remember_explicit_preferences(
        first, "Tutoie-moi", source_message_id="message-1"
    )

    assert service.safe_context(first.user) == {"address_form": "tu"}
    assert service.safe_context(second.user) == {}
    assert service.forget(first.user) == 1
    assert service.safe_context(first.user) == {}
    assert MemoryFact.objects.get(user=first.user).deleted_at is not None


@pytest.mark.django_db
@override_settings(AI_MEMORY_SUMMARY_MIN_MESSAGES=2, AI_MEMORY_SUMMARY_BATCH_SIZE=10)
def test_incremental_summary_is_source_bounded_and_idempotent():
    conversation = make_conversation(3)
    for text in ("Bonjour", "Partage ta position"):
        ConversationMessage.objects.create(
            conversation=conversation,
            direction=MessageDirection.INBOUND,
            message_type=MessageType.TEXT,
            text=text,
        )
    service = IncrementalConversationMemory(SummaryAI())

    first = service.refresh(conversation)
    second = service.refresh(conversation)

    assert first is not None
    assert second is None
    assert len(first.source_message_ids) == 2
    assert ConversationSummary.objects.filter(conversation=conversation).count() == 1


@pytest.mark.django_db
def test_user_style_profile_is_shared_across_channels_without_raw_text():
    whatsapp = make_conversation(4)
    telegram_identity = create_channel_identity(
        user=whatsapp.user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="memory-telegram-4",
    )
    telegram = get_or_create_active_conversation(
        channel_identity=telegram_identity, external_chat_id="chat-4"
    )[0]

    observe_writing_style(whatsapp, "Salut bro, tu peux vérifier ça stp ?")
    observe_writing_style(telegram, "Coucou frérot, ça marche")

    global_profile = UserCommunicationProfile.objects.get(user=whatsapp.user)
    snapshot = style_snapshot(telegram)
    assert global_profile.sample_count == 2
    assert global_profile.register == "casual"
    assert snapshot["register"] == "casual"
    assert "Salut bro" not in str(global_profile.signals)
