from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.conversations.application.orchestrator import ResponsePlan
from apps.conversations.application.services import (
    get_or_create_active_conversation,
    ingest_inbound_message,
    record_outbound_message,
)
from apps.identity.infrastructure.models import User
from apps.messaging.application.reliable_pipeline import (
    claim_inbox,
    claim_outbox,
    enqueue_inbound_turn,
    process_claimed_inbox,
    record_inbox_failure,
)
from apps.messaging.application.services import (
    create_channel_identity,
    record_inbound_message_event,
)
from apps.messaging.domain.dtos import (
    NormalizedInboundMessage,
    NormalizedOutboundMessage,
)
from apps.messaging.domain.enums import (
    InboxStatus,
    MessageStatus,
    MessageType,
    MessagingChannel,
    OutboxStatus,
)
from apps.messaging.infrastructure.models import (
    ConversationInboxItem,
    ConversationOutboxItem,
)
from apps.whatsapp.application.services import process_whatsapp_webhook


def make_conversation():
    user = User.objects.create_user(whatsapp_phone=f"+2376{User.objects.count():08d}")
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=user.whatsapp_phone,
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


def normalized(conversation, number):
    return NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id=f"wamid.pipeline.{number}",
        external_user_id=conversation.channel_identity.external_user_id,
        message_type=MessageType.TEXT,
        text=f"message {number}",
    )


def enqueue(conversation, number):
    message = normalized(conversation, number)
    event = record_inbound_message_event(message)
    inbound = ingest_inbound_message(message, event)
    return enqueue_inbound_turn(inbound=inbound, message=message, message_event=event)[
        0
    ]


@pytest.mark.django_db(transaction=True)
@override_settings(CONVERSATION_INBOX_LEASE_SECONDS=300)
def test_inbox_assigns_sequence_and_blocks_out_of_order_processing(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    conversation = make_conversation()
    first = enqueue(conversation, 1)
    second = enqueue(conversation, 2)

    assert (first.sequence, second.sequence) == (1, 2)
    assert claim_inbox(second.id) is None
    assert claim_inbox(first.id) is not None


@pytest.mark.django_db(transaction=True)
@override_settings(CONVERSATION_INBOX_LEASE_SECONDS=300)
def test_expired_worker_cannot_commit_with_stale_fencing_token(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    item = enqueue(make_conversation(), 1)
    stale_token = claim_inbox(item.id)
    ConversationInboxItem.objects.filter(pk=item.id).update(
        lease_expires_at=timezone.now()
    )
    current_token = claim_inbox(item.id)

    assert current_token != stale_token
    assert process_claimed_inbox(item.id, stale_token) == "STALE"


@pytest.mark.django_db(transaction=True)
@override_settings(CONVERSATION_INBOX_LEASE_SECONDS=300)
def test_processing_commits_response_intent_and_turn_together(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_conversation_outbox.delay", lambda *_args: None
    )
    monkeypatch.setattr(
        "apps.messaging.application.reliable_pipeline.process_inbound_conversation",
        lambda *_args, **_kwargs: SimpleNamespace(
            response=ResponsePlan(text="réponse ordonnée")
        ),
    )
    item = enqueue(make_conversation(), 1)

    assert process_claimed_inbox(item.id, claim_inbox(item.id)) == "PROCESSED"

    item.refresh_from_db()
    outbox = ConversationOutboxItem.objects.get(inbox_item=item)
    assert item.status == InboxStatus.PROCESSED
    assert item.message_event.status == MessageStatus.PROCESSED
    assert outbox.status == OutboxStatus.PENDING
    assert outbox.sequence == item.sequence
    assert outbox.idempotency_key == f"conversation-response:{item.id}"


@pytest.mark.django_db(transaction=True)
@override_settings(CONVERSATION_INBOX_MAX_ATTEMPTS=1)
def test_terminal_inbox_failure_sends_contextual_message_after_retry_budget(
    monkeypatch,
):
    from apps.ai.domain.exceptions import AITemporaryError
    from apps.conversations.domain.context import (
        ActiveJourney,
        ConversationContext,
        ConversationFlow,
        SellerOnboardingDraft,
        SellerOnboardingStage,
    )

    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    queued = []
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_conversation_outbox.delay",
        lambda *args: queued.append(args),
    )
    conversation = make_conversation()
    conversation.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION,
            store_name="Chez Louis",
        ),
    ).to_payload()
    conversation.save(update_fields=["context", "updated_at"])
    item = enqueue(conversation, 1)
    following = enqueue(conversation, 2)
    assert claim_inbox(following.id) is None
    token = claim_inbox(item.id)

    status = record_inbox_failure(item.id, token, AITemporaryError("provider down"))

    item.refresh_from_db()
    outbox = ConversationOutboxItem.objects.get(inbox_item=item)
    assert status == InboxStatus.DEAD_LETTER
    assert item.status == InboxStatus.DEAD_LETTER
    assert "création de votre boutique reste enregistrée" in outbox.payload["text"]
    assert "Je n’arrive pas à traiter votre demande" not in outbox.payload["text"]
    assert claim_inbox(following.id) is not None
    assert len(queued) == 1


@pytest.mark.django_db(transaction=True)
@override_settings(CONVERSATION_OUTBOX_LEASE_SECONDS=120)
def test_outbox_does_not_resend_when_matching_outbound_was_recorded(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    conversation = make_conversation()
    inbox = enqueue(conversation, 1)
    inbox.status = InboxStatus.PROCESSED
    inbox.save(update_fields=["status", "updated_at"])
    outbox = ConversationOutboxItem.objects.create(
        inbox_item=inbox,
        conversation=conversation,
        sequence=1,
        payload={"text": "déjà envoyée", "actions": [], "metadata": {}},
        idempotency_key=f"conversation-response:{inbox.id}",
    )
    record_outbound_message(
        conversation=conversation,
        message=NormalizedOutboundMessage(
            channel=conversation.channel,
            recipient_external_id=conversation.channel_identity.external_user_id,
            message_type=MessageType.TEXT,
            text="déjà envoyée",
            metadata={"idempotency_key": outbox.idempotency_key},
        ),
    )

    assert claim_outbox(outbox.id) is None
    outbox.refresh_from_db()
    assert outbox.status == OutboxStatus.DELIVERED


@pytest.mark.django_db(transaction=True)
@override_settings(CONVERSATION_OUTBOX_LEASE_SECONDS=120)
def test_expired_sending_lease_goes_to_dlq_instead_of_risking_duplicate(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    conversation = make_conversation()
    inbox = enqueue(conversation, 1)
    inbox.status = InboxStatus.PROCESSED
    inbox.save(update_fields=["status", "updated_at"])
    outbox = ConversationOutboxItem.objects.create(
        inbox_item=inbox,
        conversation=conversation,
        sequence=1,
        payload={"text": "livraison incertaine", "actions": [], "metadata": {}},
        idempotency_key=f"conversation-response:{inbox.id}",
        status=OutboxStatus.SENDING,
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )

    assert claim_outbox(outbox.id) is None
    outbox.refresh_from_db()
    assert outbox.status == OutboxStatus.DEAD_LETTER
    assert outbox.last_error_category == "UNCERTAIN_DELIVERY"


def whatsapp_payload(message_id="wamid.pipeline.webhook"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "phone-id"},
                            "messages": [
                                {
                                    "from": "237699887766",
                                    "id": message_id,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "Bonjour"},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


@pytest.mark.django_db(transaction=True)
@override_settings(CONVERSATION_INBOX_CHANNELS=("WHATSAPP",))
def test_enabled_webhook_only_persists_and_queues(monkeypatch):
    queued = []
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay",
        lambda *args: queued.append(args),
    )
    monkeypatch.setattr(
        "apps.whatsapp.application.services.process_inbound_conversation",
        lambda *_args, **_kwargs: pytest.fail(
            "webhook must not run business processing"
        ),
    )

    first = process_whatsapp_webhook(whatsapp_payload())
    second = process_whatsapp_webhook(whatsapp_payload())

    assert first == {
        "processed": 0,
        "duplicates": 0,
        "queued": 1,
        "statuses_updated": 0,
    }
    assert second["duplicates"] == 1
    assert ConversationInboxItem.objects.count() == 1
    assert len(queued) == 1
