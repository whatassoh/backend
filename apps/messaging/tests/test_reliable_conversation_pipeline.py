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
    deliver_claimed_outbox,
    enqueue_inbound_turn,
    process_claimed_inbox,
    record_inbox_failure,
    record_outbox_failure,
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
from apps.whatsapp.exceptions import WhatsAppAPIError, WhatsAppRateLimitError


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


def interactive_outbox(conversation, number=1):
    inbox = enqueue(conversation, number)
    inbox.status = InboxStatus.PROCESSED
    inbox.save(update_fields=["status", "updated_at"])
    return ConversationOutboxItem.objects.create(
        inbox_item=inbox,
        conversation=conversation,
        sequence=number,
        payload={
            "text": "Récapitulatif : commande toujours en cours.",
            "actions": [
                {"action_id": "order.confirm", "label": "Confirmer"},
                {"action_id": "order.modify", "label": "Modifier"},
                {"action_id": "order.cancel", "label": "Annuler"},
            ],
            "request_location": False,
            "metadata": {"review_fingerprint": "review-1"},
        },
        idempotency_key=f"conversation-response:{inbox.id}",
    )


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


@pytest.mark.django_db(transaction=True)
@override_settings(
    CONVERSATION_OUTBOX_MAX_ATTEMPTS=3,
    CONVERSATION_RETRY_BASE_SECONDS=10,
    CONVERSATION_RETRY_MAX_SECONDS=120,
)
def test_transient_provider_failure_retries_outbox(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    token = claim_outbox(outbox.id)

    status = record_outbox_failure(
        outbox.id, token, WhatsAppAPIError("outage", status_code=503)
    )

    outbox.refresh_from_db()
    assert status == OutboxStatus.FAILED_RETRYABLE
    assert outbox.last_error_category == "TRANSIENT_PROVIDER"
    assert outbox.available_at >= timezone.now() + timedelta(seconds=9)
    assert outbox.payload["actions"]


@pytest.mark.django_db(transaction=True)
@override_settings(
    CONVERSATION_OUTBOX_MAX_ATTEMPTS=3,
    CONVERSATION_RETRY_BASE_SECONDS=10,
    CONVERSATION_RETRY_MAX_SECONDS=120,
)
def test_rate_limit_retries_with_backoff(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    token = claim_outbox(outbox.id)

    status = record_outbox_failure(
        outbox.id,
        token,
        WhatsAppRateLimitError("slow down", status_code=429, retry_after=30),
    )

    outbox.refresh_from_db()
    assert status == OutboxStatus.FAILED_RETRYABLE
    assert outbox.last_error_category == "RATE_LIMIT"
    assert outbox.available_at >= timezone.now() + timedelta(seconds=29)


@pytest.mark.django_db(transaction=True)
def test_permanent_interactive_failure_creates_text_fallback(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    queued = []
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_conversation_outbox.delay",
        lambda *args: queued.append(args),
    )
    outbox = interactive_outbox(make_conversation())
    token = claim_outbox(outbox.id)

    status = record_outbox_failure(
        outbox.id,
        token,
        WhatsAppAPIError(
            "invalid interactive payload",
            status_code=400,
            response_data={"error": {"code": 100}},
        ),
    )

    outbox.refresh_from_db()
    fallback = outbox.payload["metadata"]
    assert status == OutboxStatus.PENDING
    assert outbox.payload["actions"] == []
    assert fallback["fallback"] is True
    assert fallback["fallback_for_outbox_id"] == str(outbox.id)
    assert fallback["fallback_original_response"]["actions"]
    assert len(queued) == 1


@pytest.mark.django_db(transaction=True)
def test_permanent_payload_failure_does_not_retry_identically(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_conversation_outbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    original_actions = list(outbox.payload["actions"])
    token = claim_outbox(outbox.id)

    status = record_outbox_failure(
        outbox.id,
        token,
        WhatsAppAPIError(
            "invalid interactive payload",
            status_code=400,
            response_data={"error": {"code": 100}},
        ),
    )

    outbox.refresh_from_db()
    assert status == OutboxStatus.PENDING
    assert outbox.payload["actions"] == []
    assert (
        outbox.payload["metadata"]["fallback_original_response"]["actions"]
        == original_actions
    )


@pytest.mark.django_db(transaction=True)
def test_text_fallback_contains_next_step(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_conversation_outbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    token = claim_outbox(outbox.id)

    record_outbox_failure(
        outbox.id,
        token,
        WhatsAppAPIError(
            "invalid interactive payload",
            status_code=400,
            response_data={"error": {"code": 100}},
        ),
    )

    outbox.refresh_from_db()
    assert "Je n'ai pas pu afficher les boutons" in outbox.payload["text"]
    assert "« Confirmer »" in outbox.payload["text"]
    assert "« Modifier »" in outbox.payload["text"]
    assert "« Annuler »" in outbox.payload["text"]


@pytest.mark.django_db(transaction=True)
def test_fallback_does_not_reenter_business_orchestrator(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_conversation_outbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    token = claim_outbox(outbox.id)
    record_outbox_failure(
        outbox.id,
        token,
        WhatsAppAPIError(
            "invalid interactive payload",
            status_code=400,
            response_data={"error": {"code": 100}},
        ),
    )
    monkeypatch.setattr(
        "apps.messaging.application.reliable_pipeline.process_inbound_conversation",
        lambda *_args, **_kwargs: pytest.fail(
            "fallback must not process inbound business"
        ),
    )
    sent = []
    monkeypatch.setattr(
        "apps.messaging.application.reliable_pipeline.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )

    assert deliver_claimed_outbox(outbox.id, claim_outbox(outbox.id)) == "DELIVERED"
    assert sent[0]["response"].actions == ()


@pytest.mark.django_db(transaction=True)
def test_permanent_interactive_failure_delivers_text_fallback(monkeypatch):
    """A terminal interactive rejection remains one business turn and is deliverable."""
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_conversation_outbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    record_outbox_failure(
        outbox.id,
        claim_outbox(outbox.id),
        WhatsAppAPIError(
            "invalid interactive payload",
            status_code=400,
            response_data={"error": {"code": 100}},
        ),
    )
    sent = []
    monkeypatch.setattr(
        "apps.messaging.application.reliable_pipeline.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )

    assert deliver_claimed_outbox(outbox.id, claim_outbox(outbox.id)) == "DELIVERED"

    outbox.refresh_from_db()
    assert outbox.status == OutboxStatus.DELIVERED
    assert sent[0]["response"].actions == ()
    assert "Récapitulatif" in sent[0]["response"].text
    assert "« Confirmer »" in sent[0]["response"].text
    assert outbox.payload["metadata"]["fallback_for_outbox_id"] == str(outbox.id)


@pytest.mark.django_db(transaction=True)
def test_successful_original_message_does_not_create_fallback(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    sent = []
    monkeypatch.setattr(
        "apps.messaging.application.reliable_pipeline.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )

    assert deliver_claimed_outbox(outbox.id, claim_outbox(outbox.id)) == "DELIVERED"
    outbox.refresh_from_db()
    assert outbox.status == OutboxStatus.DELIVERED
    assert not outbox.payload["metadata"].get("fallback")
    assert sent[0]["response"].actions


@pytest.mark.django_db(transaction=True)
def test_fallback_is_generated_only_once_and_never_recurses(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_conversation_outbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    token = claim_outbox(outbox.id)
    record_outbox_failure(
        outbox.id,
        token,
        WhatsAppAPIError(
            "invalid interactive payload",
            status_code=400,
            response_data={"error": {"code": 100}},
        ),
    )

    status = record_outbox_failure(
        outbox.id,
        claim_outbox(outbox.id),
        WhatsAppAPIError(
            "text rejected",
            status_code=400,
            response_data={"error": {"code": 100}},
        ),
    )

    outbox.refresh_from_db()
    assert status == OutboxStatus.DEAD_LETTER
    assert outbox.payload["metadata"]["fallback"] is True
    assert outbox.payload["metadata"]["fallback_for_outbox_id"] == str(outbox.id)


@pytest.mark.django_db(transaction=True)
def test_fallback_preserves_conversation_ordering(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_conversation_outbox.delay", lambda *_args: None
    )
    conversation = make_conversation()
    original = interactive_outbox(conversation, 1)
    following = interactive_outbox(conversation, 2)
    token = claim_outbox(original.id)
    record_outbox_failure(
        original.id,
        token,
        WhatsAppAPIError(
            "invalid interactive payload",
            status_code=400,
            response_data={"error": {"code": 100}},
        ),
    )

    assert claim_outbox(following.id) is None


@pytest.mark.django_db(transaction=True)
def test_permanent_provider_failure_without_possible_fallback_dead_letters_cleanly(
    monkeypatch,
):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    token = claim_outbox(outbox.id)

    status = record_outbox_failure(
        outbox.id,
        token,
        WhatsAppAPIError(
            "recipient unavailable",
            status_code=400,
            response_data={"error": {"code": 131026}},
        ),
    )

    outbox.refresh_from_db()
    assert status == OutboxStatus.DEAD_LETTER
    assert outbox.last_error_category == "PERMANENT_PROVIDER"
    assert not outbox.payload["metadata"].get("fallback")


@pytest.mark.django_db(transaction=True)
def test_configuration_failure_does_not_retry_aggressively(monkeypatch):
    monkeypatch.setattr(
        "apps.messaging.tasks.drain_conversation_inbox.delay", lambda *_args: None
    )
    outbox = interactive_outbox(make_conversation())
    token = claim_outbox(outbox.id)

    status = record_outbox_failure(
        outbox.id, token, WhatsAppAPIError("invalid token", status_code=401)
    )

    outbox.refresh_from_db()
    assert status == OutboxStatus.DEAD_LETTER
    assert outbox.last_error_category == "CONFIGURATION"


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
