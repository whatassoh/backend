from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point

from apps.ai.domain.understanding import (
    ExtractedItem,
    ExtractedUnit,
    MessageIntent,
    UserMessageUnderstanding,
)
from apps.catalog.application.services import create_category, create_product
from apps.catalog.domain.enums import ProductUnit
from apps.conversations.application.interruptions import (
    resume_last_task,
    suspend_active_task,
)
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import (
    ActiveJourney,
    ConversationContext,
    ConversationFlow,
)
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import MessageLocation, NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.orders.application.review import OrderReviewService
from apps.orders.application.services import (
    add_order_item,
    confirm_order,
    create_order_draft,
    remove_order_item,
    set_delivery_location,
)
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.orders.domain.events import OrderConfirmed
from apps.orders.domain.exceptions import OrderNotConfirmable
from apps.orders.infrastructure.models import Order, OrderItem
from apps.stores.application.order_confirmed import enqueue_store_matching
from common.events import clear_subscribers, subscribe


class UnderstandingQueue:
    def __init__(self, *values):
        self.values = list(values)

    def understand_normalized(self, *_args, **_kwargs):
        return self.values.pop(0) if self.values else None


def understanding(intent, *, items=(), **kwargs):
    return UserMessageUnderstanding(
        intent=intent,
        confidence=Decimal(".99"),
        items=tuple(items),
        **kwargs,
    )


def item(name="Riz", quantity="2", unit=ExtractedUnit.KG):
    return ExtractedItem(
        requested_name=name,
        quantity=Decimal(quantity),
        unit=unit,
    )


def conversation():
    user = User.objects.create_user(
        whatsapp_phone=f"+23766{User.objects.count():07d}"
    )
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=f"golden-{user.id}",
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


def inbound(text=None, *, action_id=None, message_id="golden-message", location=None):
    return NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id=message_id,
        external_user_id="golden-user",
        message_type=MessageType.LOCATION if location else (
            MessageType.INTERACTIVE if action_id else MessageType.TEXT
        ),
        text=text,
        location=location,
        metadata={"action_id": action_id} if action_id else {},
    )


def catalog_product(name="Riz"):
    category = create_category(name=f"Catégorie {name}-{User.objects.count()}")
    return create_product(name=name, category=category)


def confirmable_order(convo, *, name="Riz", quantity="2"):
    product = catalog_product(name)
    order = create_order_draft(customer=convo.user)
    add_order_item(
        order=order,
        requested_name=name,
        quantity=Decimal(quantity),
        unit=ProductUnit.KG,
        product=product,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    set_delivery_location(order, Point(9.70, 4.05, srid=4326), actor=convo.user)
    convo.context = ConversationContext(
        active_journey=ActiveJourney.BUY,
        active_order_id=str(order.id),
        flow=ConversationFlow.AWAITING_CONFIRMATION,
        metadata={"delivery_instructions_decided": True},
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    return order, product


@pytest.fixture
def matching_subscription():
    clear_subscribers()
    subscribe(OrderConfirmed, enqueue_store_matching)
    yield
    clear_subscribers()


@pytest.mark.django_db
def test_buy_intent_creates_single_building_order():
    convo = conversation()
    catalog_product()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(MessageIntent.CREATE_OR_UPDATE_ORDER, items=(item(),))
        )
    )

    outcome = app.process_message(convo, inbound("Je veux acheter 2 kg de riz"))

    assert Order.objects.filter(customer=convo.user).count() == 1
    assert "position" in outcome.response.text.casefold()


@pytest.mark.django_db(transaction=True)
def test_simple_buy_e2e_reaches_confirmed_system_wait(monkeypatch, matching_subscription):
    """The customer can complete BUY without a manual "continue" recovery."""

    convo = conversation()
    catalog_product()
    calls = []
    monkeypatch.setattr(
        "apps.stores.tasks.start_store_matching_for_order.delay",
        lambda *args: calls.append(args),
    )
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(MessageIntent.CREATE_OR_UPDATE_ORDER, items=(item(),))
        )
    )

    first = app.process_message(
        convo, inbound("Je veux acheter 2 kg de riz", message_id="simple-buy")
    )
    location = app.process_message(
        convo,
        inbound(
            message_id="simple-location",
            location=MessageLocation(latitude=4.06, longitude=9.71),
        ),
    )
    review = app.process_message(
        convo,
        inbound(
            action_id="order.skip_delivery_instructions",
            message_id="simple-skip-instructions",
        ),
    )
    confirmed = app.process_message(
        convo, inbound(action_id="order.confirm", message_id="simple-confirm")
    )

    order = Order.objects.get(customer=convo.user)
    assert "position" in first.response.text.casefold()
    assert "indication" in location.response.text.casefold()
    assert "order.confirm" in {action.action_id for action in review.response.actions}
    assert order.status == OrderStatus.CONFIRMED
    assert confirmed.response.next_step.kind == "SYSTEM_WAIT"
    assert len(calls) == 1


@pytest.mark.django_db(transaction=True)
def test_multi_product_replacement_e2e_reaches_confirmed_system_wait(
    monkeypatch, matching_subscription
):
    convo = conversation()
    catalog_product("Riz")
    calls = []
    monkeypatch.setattr(
        "apps.stores.tasks.start_store_matching_for_order.delay",
        lambda *args: calls.append(args),
    )
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(
                MessageIntent.CREATE_OR_UPDATE_ORDER,
                items=(
                    item("Riz", "2"),
                    item("Huile inconnue", "4", ExtractedUnit.BOTTLE),
                ),
            ),
            understanding(
                MessageIntent.CREATE_OR_UPDATE_ORDER, items=(item("Riz", "1"),)
            ),
        )
    )

    unmatched = app.process_message(
        convo, inbound("Je veux acheter 2 kg de riz et 4 bouteilles d'huile", message_id="multi-buy")
    )
    app.process_message(
        convo,
        inbound(action_id="order.unmatched.replace", message_id="multi-replace-action"),
    )
    replacement = app.process_message(
        convo, inbound("1 kg de riz", message_id="multi-replacement")
    )
    app.process_message(
        convo,
        inbound(
            message_id="multi-location",
            location=MessageLocation(latitude=4.06, longitude=9.71),
        ),
    )
    review = app.process_message(
        convo,
        inbound(
            action_id="order.skip_delivery_instructions",
            message_id="multi-skip-instructions",
        ),
    )
    confirmed = app.process_message(
        convo, inbound(action_id="order.confirm", message_id="multi-confirm")
    )

    order = Order.objects.get(customer=convo.user)
    rows = list(OrderItem.objects.filter(order=order))
    assert "remplacer" in unmatched.response.text.casefold()
    assert "position" in replacement.response.text.casefold()
    assert len(rows) == 1
    assert rows[0].quantity == Decimal("3")
    assert rows[0].resolution_status == OrderItemResolutionStatus.MATCHED
    assert "order.confirm" in {action.action_id for action in review.response.actions}
    assert order.status == OrderStatus.CONFIRMED
    assert confirmed.response.next_step.kind == "SYSTEM_WAIT"
    assert len(calls) == 1


@pytest.mark.django_db
def test_second_product_updates_same_draft_order():
    convo = conversation()
    order, _ = confirmable_order(convo)
    oil = catalog_product("Huile")
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())
    context = ConversationContext.from_payload(convo.context)

    app._handle_order_items(
        convo,
        context,
        understanding(MessageIntent.CREATE_OR_UPDATE_ORDER, items=(item("Huile", "1", ExtractedUnit.BOTTLE),)),
        "golden-second-product",
    )

    assert OrderItem.objects.filter(order=order).count() == 2
    assert oil.name == "Huile"


@pytest.mark.django_db
def test_existing_product_update_uses_domain_merge_rule():
    convo = conversation()
    order, product = confirmable_order(convo)
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())
    resolution = app.grounding_service.ground_item(item("Riz", "3"))

    app.order_building_service.add(order, item("Riz", "3"), resolution, actor=convo.user)

    rows = OrderItem.objects.filter(order=order, product=product)
    assert rows.count() == 1
    assert rows.get().quantity == Decimal("5")


@pytest.mark.django_db
def test_unmatched_item_blocks_confirmation():
    convo = conversation()
    order = create_order_draft(customer=convo.user)
    add_order_item(
        order=order,
        requested_name="Produit inconnu",
        quantity=Decimal("1"),
        unit=ProductUnit.UNIT,
        resolution_status=OrderItemResolutionStatus.UNMATCHED,
    )
    set_delivery_location(order, Point(9.70, 4.05, srid=4326))

    assert "unmatched_items" in OrderReviewService().build_review(order).readiness.blocking_issues
    with pytest.raises(OrderNotConfirmable):
        confirm_order(order)


@pytest.mark.django_db
def test_unmatched_item_can_be_removed():
    convo = conversation()
    order = create_order_draft(customer=convo.user)
    row = add_order_item(
        order=order,
        requested_name="Inconnu",
        quantity=Decimal("1"),
        unit=ProductUnit.UNIT,
        resolution_status=OrderItemResolutionStatus.UNMATCHED,
    )

    remove_order_item(row)

    assert not OrderItem.objects.filter(order=order).exists()


@pytest.mark.django_db
def test_unmatched_item_can_be_replaced():
    convo = conversation()
    product = catalog_product()
    order = create_order_draft(customer=convo.user)
    row = add_order_item(
        order=order,
        requested_name="Inconnu",
        quantity=Decimal("1"),
        unit=ProductUnit.KG,
        resolution_status=OrderItemResolutionStatus.UNMATCHED,
    )
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())

    app.order_building_service.replace(
        row, item("Riz", "1"), app.grounding_service.ground_item(item("Riz", "1"))
    )

    row.refresh_from_db()
    assert row.product == product
    assert row.resolution_status == OrderItemResolutionStatus.MATCHED


@pytest.mark.django_db
def test_order_without_items_cannot_be_confirmed():
    order = create_order_draft(customer=conversation().user)
    set_delivery_location(order, Point(9.70, 4.05, srid=4326))

    with pytest.raises(OrderNotConfirmable):
        confirm_order(order)


@pytest.mark.django_db
def test_order_without_required_location_cannot_be_confirmed():
    convo = conversation()
    order = create_order_draft(customer=convo.user)
    product = catalog_product()
    add_order_item(
        order=order,
        requested_name="Riz",
        quantity=Decimal("1"),
        unit=ProductUnit.KG,
        product=product,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )

    with pytest.raises(OrderNotConfirmable):
        confirm_order(order)


@pytest.mark.django_db
def test_location_advances_customer_flow():
    convo = conversation()
    order, _ = confirmable_order(convo)
    convo.context = ConversationContext(
        active_journey=ActiveJourney.BUY,
        active_order_id=str(order.id),
        flow=ConversationFlow.AWAITING_LOCATION,
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])

    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue()
    ).process_message(
        convo,
        inbound(location=MessageLocation(latitude=4.06, longitude=9.71)),
    )

    order.refresh_from_db()
    assert order.delivery_location is not None
    assert "indication" in outcome.response.text.casefold()


@pytest.mark.django_db
def test_skip_delivery_instructions_advances_to_recap():
    convo = conversation()
    order, _ = confirmable_order(convo)
    convo.context = ConversationContext(
        active_journey=ActiveJourney.BUY,
        active_order_id=str(order.id),
        flow=ConversationFlow.ORDER_BUILDING,
        metadata={"awaiting_delivery_instructions": True},
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])

    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue()
    ).process_message(convo, inbound(action_id="order.skip_delivery_instructions"))

    assert "Récapitulatif" in outcome.response.text
    assert "order.confirm" in {action.action_id for action in outcome.response.actions}


@pytest.mark.django_db
def test_delivery_instructions_are_persisted():
    convo = conversation()
    order, _ = confirmable_order(convo)
    context = ConversationContext(
        active_journey=ActiveJourney.BUY,
        active_order_id=str(order.id),
        flow=ConversationFlow.ORDER_BUILDING,
        metadata={"awaiting_delivery_instructions": True},
    )

    ConversationalOrchestrator(understanding_service=UnderstandingQueue())._handle_delivery_instructions(
        convo, context, "Portail bleu, appelez en arrivant", "golden-instructions"
    )

    order.refresh_from_db()
    assert order.delivery_instructions == "Portail bleu, appelez en arrivant"


@pytest.mark.django_db
def test_order_recap_matches_persisted_order():
    convo = conversation()
    order, _ = confirmable_order(convo, quantity="3")
    plan = ConversationalOrchestrator(understanding_service=UnderstandingQueue())._recap(order)

    assert "3" in plan.text
    assert "Riz" in plan.text
    assert "order.confirm" in {action.action_id for action in plan.actions}


@pytest.mark.django_db
def test_modify_order_returns_to_valid_recap():
    convo = conversation()
    order, _ = confirmable_order(convo)
    row = OrderItem.objects.get(order=order)
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())
    app.order_building_service.update(row, quantity=Decimal("3"), actor=convo.user)

    outcome = app._modified_outcome(convo, order, "order_item_updated")

    assert "3" in outcome.response.text
    assert "order.confirm" in {action.action_id for action in outcome.response.actions}


@pytest.mark.django_db(transaction=True)
def test_confirm_order_is_idempotent(monkeypatch, matching_subscription):
    convo = conversation()
    order, _ = confirmable_order(convo)
    calls = []
    monkeypatch.setattr(
        "apps.stores.tasks.start_store_matching_for_order.delay",
        lambda *args: calls.append(args),
    )
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())

    first = app.process_message(convo, inbound(action_id="order.confirm", message_id="confirm-1"))
    second = app.process_message(convo, inbound(action_id="order.confirm", message_id="confirm-2"))

    order.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED
    assert first.actions_performed == ("order_confirmed",)
    assert second.actions_performed == ("order_confirmation_replayed",)
    assert len(calls) == 1


@pytest.mark.django_db(transaction=True)
def test_confirmed_order_starts_store_matching_once(monkeypatch, matching_subscription):
    convo = conversation()
    order, _ = confirmable_order(convo)
    calls = []
    monkeypatch.setattr(
        "apps.stores.tasks.start_store_matching_for_order.delay",
        lambda *args: calls.append(args),
    )

    confirm_order(order, actor=convo.user, correlation_id="golden-matching")
    confirm_order(order, actor=convo.user, correlation_id="golden-matching-retry")

    assert len(calls) == 1


@pytest.mark.django_db
def test_confirmation_sets_customer_to_system_wait(monkeypatch):
    convo = conversation()
    order, _ = confirmable_order(convo)
    monkeypatch.setattr("apps.stores.tasks.start_store_matching_for_order.delay", lambda *_: None)

    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue()
    ).process_message(convo, inbound(action_id="order.confirm"))

    assert outcome.response.next_step.kind == "SYSTEM_WAIT"
    assert "prochaine étape" in outcome.response.text


@pytest.mark.django_db
def test_cancel_building_order_cleans_active_context():
    convo = conversation()
    order, _ = confirmable_order(convo)
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())

    app.process_message(convo, inbound(action_id="order.cancel"))

    order.refresh_from_db()
    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert order.status == OrderStatus.CANCELLED
    assert context.active_order_id is None
    assert context.pending_clarification is None


@pytest.mark.django_db
def test_replayed_inbound_does_not_duplicate_order_items():
    convo = conversation()
    catalog_product()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understanding(MessageIntent.CREATE_OR_UPDATE_ORDER, items=(item(),))
        )
    )
    message = inbound("Je veux acheter 2 kg de riz", message_id="same-inbound")

    app.process_message(convo, message)
    app.process_message(convo, message)

    assert OrderItem.objects.filter(order__customer=convo.user).count() == 1


def test_resumed_buy_restores_correct_step():
    context = ConversationContext(
        active_journey=ActiveJourney.BUY,
        flow=ConversationFlow.AWAITING_CONFIRMATION,
        active_order_id="order-id",
        metadata={"delivery_instructions_decided": True},
    )
    suspended, _ = suspend_active_task(context, reason="golden-path")

    restored = resume_last_task(suspended)

    assert restored.context.flow == ConversationFlow.AWAITING_CONFIRMATION
    assert restored.context.active_order_id == "order-id"
