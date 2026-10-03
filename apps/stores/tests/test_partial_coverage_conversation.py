import json
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point

from apps.ai.domain.understanding import MessageIntent, UserMessageUnderstanding
from apps.catalog.application.services import (
    add_product_to_store,
    create_category,
    create_product,
)
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.domain.context import ConversationContext
from apps.conversations.infrastructure.models import Conversation
from apps.identity.infrastructure.models import User
from apps.messaging.domain.dtos import NormalizedInboundMessage, ProviderSendResult
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.messaging.application.services import create_channel_identity
from apps.orders.application.services import (
    add_order_item,
    confirm_order,
    create_order_draft,
    set_delivery_location,
)
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.orders.infrastructure.models import OrderItem
from apps.stores.application.matching import StoreMatchingOrchestrator
from apps.stores.application.partial_coverage import PartialCoverageConsentService
from apps.stores.application.partial_coverage_actions import (
    decode_partial_coverage_action,
    encode_partial_coverage_action,
)
from apps.stores.application.services import create_store
from apps.stores.domain.enums import PartialStoreCoverageProposalStatus
from apps.stores.infrastructure.models import PartialStoreCoverageProposal, StoreOffer
from apps.stores.tasks import deliver_partial_coverage_proposal
from apps.telegram.adapters.inbound import parse_telegram_update
from apps.whatsapp.adapters.inbound import parse_whatsapp_webhook
from common.events import clear_subscribers


@pytest.fixture(autouse=True)
def isolate_event_bus():
    clear_subscribers()
    yield
    clear_subscribers()
    from apps.orders.domain.events import OrderConfirmed
    from apps.stores.application.order_confirmed import enqueue_store_matching
    from common.events import subscribe

    subscribe(OrderConfirmed, enqueue_store_matching)


def partial_order(monkeypatch):
    monkeypatch.setattr(
        StoreMatchingOrchestrator,
        "_notify_partial_proposal",
        staticmethod(lambda *_args: None),
    )
    customer = User.objects.create_user(
        whatsapp_phone=f"+237693{User.objects.count():06d}"
    )
    identity = create_channel_identity(
        user=customer,
        channel=MessagingChannel.TELEGRAM,
        external_user_id=f"pc-{customer.id}",
    )
    conversation = Conversation.objects.create(
        user=customer,
        channel=MessagingChannel.TELEGRAM,
        channel_identity=identity,
        external_chat_id=f"pc-{customer.id}",
    )
    order = create_order_draft(customer=customer)
    category = create_category(name=f"Partial bridge {order.id}")
    names = ("Riz", "Huile", "Sucre", "Sel")
    products = {
        name: create_product(name=f"{name} {order.id}", category=category)
        for name in names
    }
    rows = {}
    for name, product in products.items():
        rows[name] = add_order_item(
            order=order,
            requested_name=name,
            quantity=1,
            unit=ProductUnit.UNIT,
            product=product,
            resolution_status=OrderItemResolutionStatus.MATCHED,
        )
    set_delivery_location(order, Point(9.7357, 4.0907, srid=4326))
    seller = User.objects.create_user(
        whatsapp_phone=f"+237694{User.objects.count():06d}"
    )
    create_channel_identity(
        user=seller,
        channel=MessagingChannel.TELEGRAM,
        external_user_id=f"seller-{seller.id}",
    )
    store = create_store(
        name=f"Akwa {order.id}",
        city="Douala",
        location=Point(9.736, 4.0907, srid=4326),
        created_by=seller,
    )
    for name in ("Riz", "Sucre"):
        add_product_to_store(
            store=store,
            product=products[name],
            availability_status=ProductAvailability.AVAILABLE,
        )
    order = confirm_order(order)
    StoreMatchingOrchestrator().start_matching(order.id)
    return order, customer, conversation, store, products, rows


def text_message(text, message_id="partial-text"):
    return NormalizedInboundMessage(
        channel=MessagingChannel.TELEGRAM,
        external_message_id=message_id,
        external_user_id="pc-user",
        external_chat_id="pc-chat",
        message_type=MessageType.TEXT,
        text=text,
    )


class UnknownUnderstanding:
    def understand_normalized(self, *_args, **_kwargs):
        return UserMessageUnderstanding(
            intent=MessageIntent.UNKNOWN, confidence=Decimal(".99")
        )


@pytest.mark.django_db(transaction=True)
def test_partial_coverage_telegram_is_interactive_and_task_result_is_json_safe(
    monkeypatch,
):
    order, _, conversation, _, _, _ = partial_order(monkeypatch)
    sent = []
    monkeypatch.setattr(
        "apps.stores.tasks.send_conversation_interactive",
        lambda **kwargs: (
            sent.append(kwargs)
            or ProviderSendResult(channel="TELEGRAM", external_message_id="tg-1")
        ),
    )

    result = deliver_partial_coverage_proposal.run(str(order.id))

    assert result == {"status": "SENT", "external_message_id": "tg-1"}
    json.dumps(result)
    assert sent[0]["conversation"].id == conversation.id
    assert [item["label"] for item in sent[0]["actions"]] == [
        "Continuer sans",
        "Modifier",
        "Annuler",
    ]


@pytest.mark.django_db
def test_partial_coverage_action_round_trip_contains_proposal_id(monkeypatch):
    order, *_ = partial_order(monkeypatch)
    proposal = PartialStoreCoverageProposal.objects.get(order=order)
    action = encode_partial_coverage_action("partial_coverage.accept", proposal.id)

    assert len(action.encode()) <= 64
    assert decode_partial_coverage_action(action) == (
        "partial_coverage.accept",
        proposal.id,
    )
    assert decode_partial_coverage_action("partial_coverage.accept:not-a-uuid") is None


@pytest.mark.django_db(transaction=True)
def test_telegram_callback_consumes_partial_proposal_and_restarts_matching(monkeypatch):
    order, customer, conversation, store, products, rows = partial_order(monkeypatch)
    proposal = PartialStoreCoverageProposal.objects.get(order=order)
    # The callback must traverse normalized provider input and the conversation router.
    enqueued = []
    monkeypatch.setattr(
        PartialCoverageConsentService,
        "_enqueue",
        staticmethod(lambda order_id, *_: enqueued.append(order_id)),
    )
    monkeypatch.setattr(
        "apps.stores.tasks.deliver_store_offer_notification.delay", lambda *_: None
    )
    callback = encode_partial_coverage_action("partial_coverage.accept", proposal.id)
    inbound = parse_telegram_update(
        {
            "update_id": 9901,
            "callback_query": {
                "id": "partial-callback-1",
                "from": {"id": "pc-user"},
                "message": {"message_id": 1, "chat": {"id": "pc-chat"}},
                "data": callback,
            },
        }
    )[0]

    outcome = ConversationalOrchestrator(
        understanding_service=UnknownUnderstanding()
    ).process_message(conversation, inbound)

    proposal.refresh_from_db()
    order.refresh_from_db()
    assert outcome.metadata["partial_coverage"] == "ACCEPTED"
    assert proposal.status == PartialStoreCoverageProposalStatus.ACCEPTED
    assert proposal.consumed_at is not None
    assert set(OrderItem.objects.filter(order=order).values_list("id", flat=True)) == {
        rows["Riz"].id,
        rows["Sucre"].id,
    }
    assert enqueued == [order.id]
    # This is the worker-side continuation requested by the existing service.
    StoreMatchingOrchestrator().start_matching(order.id)
    assert StoreOffer.objects.filter(
        order=order, store=store, status="PENDING"
    ).exists()

    replay = ConversationalOrchestrator(
        understanding_service=UnknownUnderstanding()
    ).process_message(
        conversation,
        NormalizedInboundMessage(
            channel=MessagingChannel.TELEGRAM,
            external_message_id="partial-callback-replay",
            external_user_id="pc-user",
            external_chat_id="pc-chat",
            message_type=MessageType.INTERACTIVE,
            metadata={"action_id": callback},
        ),
    )
    assert replay.metadata["replay"] is True
    assert StoreOffer.objects.filter(order=order).count() == 1


@pytest.mark.django_db(transaction=True)
def test_partial_coverage_plain_text_continue_is_explicit_and_ambiguous_text_is_safe(
    monkeypatch,
):
    order, _, conversation, _, _, rows = partial_order(monkeypatch)
    monkeypatch.setattr(
        PartialCoverageConsentService, "_enqueue", staticmethod(lambda *_: None)
    )
    app = ConversationalOrchestrator(understanding_service=UnknownUnderstanding())

    ambiguous = app.process_message(
        conversation, text_message("continue", "partial-ambiguous")
    )
    assert (
        PartialStoreCoverageProposal.objects.get(order=order).status
        == PartialStoreCoverageProposalStatus.PENDING
    )
    assert ambiguous.metadata.get("partial_coverage") is None

    accepted = app.process_message(
        conversation, text_message("Continuer sans les produits", "partial-continue")
    )
    assert accepted.metadata["partial_coverage"] == "ACCEPTED"
    assert set(OrderItem.objects.filter(order=order).values_list("id", flat=True)) == {
        rows["Riz"].id,
        rows["Sucre"].id,
    }


@pytest.mark.django_db(transaction=True)
def test_whatsapp_callback_uses_the_same_self_contained_partial_action(monkeypatch):
    order, _, conversation, _, _, _ = partial_order(monkeypatch)
    conversation.channel = MessagingChannel.WHATSAPP
    conversation.save(update_fields=["channel", "updated_at"])
    proposal = PartialStoreCoverageProposal.objects.get(order=order)
    monkeypatch.setattr(
        PartialCoverageConsentService, "_enqueue", staticmethod(lambda *_: None)
    )
    callback = encode_partial_coverage_action("partial_coverage.accept", proposal.id)
    inbound = parse_whatsapp_webhook(
        {
            "entry": [
                {
                    "id": "waba",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "phone"},
                                "messages": [
                                    {
                                        "id": "wamid.partial",
                                        "from": "237699000000",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {
                                                "id": callback,
                                                "title": "Continuer sans",
                                            },
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    )[0]

    outcome = ConversationalOrchestrator(
        understanding_service=UnknownUnderstanding()
    ).process_message(conversation, inbound)

    proposal.refresh_from_db()
    assert inbound.metadata["action_id"] == callback
    assert outcome.metadata["partial_coverage"] == "ACCEPTED"
    assert proposal.status == PartialStoreCoverageProposalStatus.ACCEPTED


class ConfirmUnderstanding:
    def understand_normalized(self, *_args, **_kwargs):
        return UserMessageUnderstanding(
            intent=MessageIntent.CONFIRM, confidence=Decimal(".99")
        )


@pytest.mark.django_db
def test_confirm_while_awaiting_partial_coverage_restates_decision(monkeypatch):
    order, _, conversation, _, _, _ = partial_order(monkeypatch)
    conversation.context = ConversationContext(
        metadata={"last_confirmed_order_id": str(order.id)}
    ).to_payload()
    conversation.save(update_fields=["context", "updated_at"])

    outcome = ConversationalOrchestrator(
        understanding_service=ConfirmUnderstanding()
    ).process_message(
        conversation, text_message("Confirmer ma commande", "partial-confirm")
    )

    assert "produits indisponibles" in outcome.response.text
    assert [item.label for item in outcome.response.actions] == [
        "Continuer sans",
        "Modifier",
        "Annuler",
    ]


@pytest.mark.django_db
def test_order_status_falls_back_to_awaiting_partial_coverage_without_active_order_id(
    monkeypatch,
):
    order, _, conversation, _, _, _ = partial_order(monkeypatch)
    conversation.context = ConversationContext(active_order_id=None).to_payload()
    conversation.save(update_fields=["context", "updated_at"])

    class StatusUnderstanding:
        def understand_normalized(self, *_args, **_kwargs):
            return UserMessageUnderstanding(
                intent=MessageIntent.ORDER_STATUS, confidence=Decimal(".99")
            )

    outcome = ConversationalOrchestrator(
        understanding_service=StatusUnderstanding()
    ).process_message(
        conversation, text_message("Quel est l'état de ma commande ?", "partial-status")
    )
    assert str(order.reference) in outcome.response.text
    assert outcome.metadata["order_status"] == OrderStatus.AWAITING_PARTIAL_COVERAGE
