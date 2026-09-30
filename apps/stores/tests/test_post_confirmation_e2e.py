"""Post-confirmation business E2E tests; provider delivery is deliberately mocked."""
import pytest
from django.contrib.gis.geos import Point

from apps.catalog.application.services import add_product_to_store, create_category, create_product
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel, NotificationKind
from apps.messaging.domain.dtos import ProviderSendResult
from apps.messaging.infrastructure.models import OperationalNotification
from apps.orders.application.services import add_order_item, confirm_order, create_order_draft, set_delivery_location
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.orders.domain.events import OrderReadyForPickup
from apps.stores.application.matching import StoreMatchingOrchestrator
from apps.stores.application.partial_coverage import PartialCoverageConsentService
from apps.stores.application.services import create_store
from apps.stores.domain.enums import PartialStoreCoverageProposalStatus
from apps.stores.infrastructure.models import PartialStoreCoverageProposal, StoreOffer
from apps.stores.infrastructure.models import StoreOfferNotification
from apps.stores.application.offer_notifications import StoreOfferNotificationRenderer
from apps.stores.tasks import _dispatch
from apps.telegram.adapters.inbound import parse_telegram_update
from apps.whatsapp.adapters.inbound import parse_whatsapp_webhook
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from common.events import clear_subscribers, subscribe
from apps.orders.domain.events import OrderConfirmed, OrderFailed
from apps.stores.application.order_confirmed import enqueue_store_matching
from apps.orders.application.customer_notifications import notify_order_failed


@pytest.fixture(autouse=True)
def event_bridge(monkeypatch):
    """Assert the bridge's enqueue request, then execute the task deterministically."""
    clear_subscribers()
    subscribe(OrderConfirmed, enqueue_store_matching)
    subscribe(OrderFailed, notify_order_failed)
    monkeypatch.setattr("apps.stores.tasks.start_store_matching_for_order.delay", lambda *_args: None)
    monkeypatch.setattr("apps.stores.tasks.deliver_store_offer_notification.delay", lambda *_args: None)
    monkeypatch.setattr("apps.messaging.tasks.deliver_operational_notification.delay", lambda *_args: None)
    monkeypatch.setattr(StoreMatchingOrchestrator, "_notify_partial_proposal", staticmethod(lambda *_args: None))
    yield
    clear_subscribers()


def order_with_products(names=("Riz", "Huile", "Gaz")):
    customer = User.objects.create_user(whatsapp_phone=f"+237691{User.objects.count():06d}")
    create_channel_identity(user=customer, channel=MessagingChannel.TELEGRAM, external_user_id=f"customer-{customer.id}")
    order = create_order_draft(customer=customer)
    category = create_category(name=f"E2E {order.id}")
    products = {name: create_product(name=f"{name} {order.id}", category=category) for name in names}
    for name, product in products.items():
        add_order_item(order=order, requested_name=name, quantity=1, unit=ProductUnit.UNIT,
                       product=product, resolution_status=OrderItemResolutionStatus.MATCHED)
    set_delivery_location(order, Point(9.7357, 4.0907, srid=4326))
    return order, customer, products


def catalog_store(name, products, *, owner=None):
    store = create_store(name=name, city="Douala", location=Point(9.736, 4.0907, srid=4326), created_by=owner)
    for product in products:
        add_product_to_store(store=store, product=product, availability_status=ProductAvailability.AVAILABLE)
    return store


@pytest.mark.django_db(transaction=True)
def test_confirm_full_store_offer_then_seller_accepts_and_customer_is_notified(monkeypatch):
    order, customer, products = order_with_products()
    seller = User.objects.create_user(whatsapp_phone="+237698000001")
    create_channel_identity(user=seller, channel=MessagingChannel.TELEGRAM, external_user_id="seller-e2e")
    store = catalog_store("Full E2E", products.values(), owner=seller)
    confirm_order(order)

    StoreMatchingOrchestrator().start_matching(order.id)
    offer = StoreOffer.objects.get(order=order, store=store)
    StoreMatchingOrchestrator().offers.accept_offer(offer)

    order.refresh_from_db()
    assert order.status == OrderStatus.STORE_ACCEPTED
    assert OperationalNotification.objects.filter(recipient=customer, kind=NotificationKind.ORDER_STORE_ACCEPTED).count() == 1


@pytest.mark.django_db(transaction=True)
def test_telegram_store_offer_button_reaches_conversation_and_accepts_exact_offer(monkeypatch):
    order, customer, products = order_with_products(names=("Huile",))
    seller = User.objects.create_user(whatsapp_phone="+237698000011")
    create_channel_identity(
        user=seller, channel=MessagingChannel.TELEGRAM, external_user_id="seller-button-e2e"
    )
    store = catalog_store("Telegram Button E2E", products.values(), owner=seller)
    confirm_order(order)
    StoreMatchingOrchestrator().start_matching(order.id)
    offer = StoreOffer.objects.get(order=order, store=store)
    notification = StoreOfferNotification.objects.select_related("conversation").get(offer=offer)

    sent = []
    monkeypatch.setattr(
        "apps.messaging.application.orchestration.send",
        lambda message: sent.append(message) or ProviderSendResult(channel=message.channel),
    )
    instruction = StoreOfferNotificationRenderer().render(notification)
    _dispatch(notification, instruction, "telegram-e2e")
    outbound = sent[0]
    assert outbound.message_type == "INTERACTIVE"
    callback_id = outbound.metadata["actions"][0]["id"]
    assert callback_id == f"store_offer.accept:{offer.id}"

    inbound = parse_telegram_update({
        "update_id": 42,
        "callback_query": {
            "id": "callback-42",
            "from": {"id": "seller-button-e2e"},
            "message": {"chat": {"id": "seller-button-e2e"}},
            "data": callback_id,
        },
    })[0]

    class UnderstandingMustNotRun:
        def understand_normalized(self, *_args, **_kwargs):
            raise AssertionError("structured StoreOffer actions must bypass AI")

    ready_events = []
    subscribe(OrderReadyForPickup, ready_events.append)
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingMustNotRun()
    )
    outcome = app.process_message(notification.conversation, inbound)
    offer.refresh_from_db()
    order.refresh_from_db()
    assert outcome.metadata["store_offer"] == "ACCEPTED"
    assert offer.status == "ACCEPTED"
    assert order.status == OrderStatus.STORE_ACCEPTED
    assert OperationalNotification.objects.filter(
        recipient=customer, kind=NotificationKind.ORDER_STORE_ACCEPTED
    ).count() == 1
    start_id = outcome.response.actions[0].action_id
    assert start_id == f"store_order.start:{order.id}"
    start = parse_telegram_update({
        "update_id": 44,
        "callback_query": {"id": "callback-44", "from": {"id": "seller-button-e2e"},
            "message": {"chat": {"id": "seller-button-e2e"}}, "data": start_id},
    })[0]
    started = app.process_message(notification.conversation, start)
    order.refresh_from_db()
    assert order.status == OrderStatus.PREPARING
    ready_id = started.response.actions[0].action_id
    assert ready_id == f"store_order.ready:{order.id}"
    ready = parse_telegram_update({
        "update_id": 45,
        "callback_query": {"id": "callback-45", "from": {"id": "seller-button-e2e"},
            "message": {"chat": {"id": "seller-button-e2e"}}, "data": ready_id},
    })[0]
    app.process_message(notification.conversation, ready)
    order.refresh_from_db()
    assert order.status == OrderStatus.READY_FOR_PICKUP
    assert len(ready_events) == 1


@pytest.mark.django_db(transaction=True)
def test_telegram_reject_callback_advances_once_to_next_store(monkeypatch):
    order, _, products = order_with_products(names=("Riz",))
    seller_a = User.objects.create_user(whatsapp_phone="+237698000021")
    seller_b = User.objects.create_user(whatsapp_phone="+237698000022")
    create_channel_identity(user=seller_a, channel=MessagingChannel.TELEGRAM, external_user_id="seller-a")
    create_channel_identity(user=seller_b, channel=MessagingChannel.TELEGRAM, external_user_id="seller-b")
    store_a = catalog_store("A Telegram", products.values(), owner=seller_a)
    store_b = catalog_store("B Telegram", products.values(), owner=seller_b)
    confirm_order(order)
    StoreMatchingOrchestrator().start_matching(order.id)
    offer_a = StoreOffer.objects.get(order=order)
    next_store = store_b if offer_a.store_id == store_a.id else store_a
    notification = StoreOfferNotification.objects.get(offer=offer_a)
    callback_id = notification.presentation_snapshot["actions"][1]["id"]
    inbound = parse_telegram_update({
        "update_id": 43,
        "callback_query": {
            "id": "callback-43", "from": {"id": "seller-a"},
            "message": {"chat": {"id": "seller-a"}}, "data": callback_id,
        },
    })[0]

    class UnderstandingMustNotRun:
        def understand_normalized(self, *_args, **_kwargs):
            raise AssertionError

    outcome = ConversationalOrchestrator(understanding_service=UnderstandingMustNotRun()).process_message(
        notification.conversation, inbound
    )
    offer_a.refresh_from_db()
    assert outcome.metadata["store_offer"] == "REJECTED"
    assert offer_a.status == "REJECTED"
    assert StoreOffer.objects.filter(order=order, store=next_store, status="PENDING").count() == 1


@pytest.mark.django_db(transaction=True)
def test_whatsapp_open_reply_button_callback_accepts_exact_offer(monkeypatch):
    order, _, products = order_with_products(names=("Riz",))
    seller = User.objects.create_user(whatsapp_phone="+237698000031")
    create_channel_identity(user=seller, channel=MessagingChannel.WHATSAPP, external_user_id="237698000031")
    store = catalog_store("WhatsApp Open", products.values(), owner=seller)
    confirm_order(order)
    StoreMatchingOrchestrator().start_matching(order.id)
    offer = StoreOffer.objects.get(order=order, store=store)
    notification = StoreOfferNotification.objects.get(offer=offer)
    callback_id = notification.presentation_snapshot["actions"][0]["id"]
    inbound = parse_whatsapp_webhook({
        "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": "phone"},
            "messages": [{"id": "wamid.offer", "from": "237698000031", "type": "interactive",
                "interactive": {"type": "button_reply", "button_reply": {"id": callback_id, "title": "Accepter"}}}],
        }}]}],
    })[0]

    class UnderstandingMustNotRun:
        def understand_normalized(self, *_args, **_kwargs):
            raise AssertionError

    ready_events = []
    subscribe(OrderReadyForPickup, ready_events.append)
    app = ConversationalOrchestrator(understanding_service=UnderstandingMustNotRun())
    outcome = app.process_message(
        notification.conversation, inbound
    )
    offer.refresh_from_db()
    order.refresh_from_db()
    assert outcome.metadata["offer_id"] == str(offer.id)
    assert offer.status == "ACCEPTED"
    assert order.status == OrderStatus.STORE_ACCEPTED
    start_id = outcome.response.actions[0].action_id
    start = parse_whatsapp_webhook({
        "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": "phone"},
            "messages": [{"id": "wamid.start", "from": "237698000031", "type": "interactive",
                "interactive": {"type": "button_reply", "button_reply": {"id": start_id, "title": "Démarrer"}}}],
        }}]}],
    })[0]
    started = app.process_message(notification.conversation, start)
    order.refresh_from_db()
    assert order.status == OrderStatus.PREPARING
    ready_id = started.response.actions[0].action_id
    ready = parse_whatsapp_webhook({
        "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": "phone"},
            "messages": [{"id": "wamid.ready", "from": "237698000031", "type": "interactive",
                "interactive": {"type": "button_reply", "button_reply": {"id": ready_id, "title": "Prête"}}}],
        }}]}],
    })[0]
    app.process_message(notification.conversation, ready)
    order.refresh_from_db()
    assert order.status == OrderStatus.READY_FOR_PICKUP
    assert len(ready_events) == 1


@pytest.mark.django_db(transaction=True)
def test_whatsapp_template_reply_reopens_and_represents_only_live_offer(monkeypatch):
    order, _, products = order_with_products(names=("Riz",))
    seller = User.objects.create_user(whatsapp_phone="+237698000041")
    create_channel_identity(user=seller, channel=MessagingChannel.WHATSAPP, external_user_id="237698000041")
    store = catalog_store("WhatsApp Reopen", products.values(), owner=seller)
    confirm_order(order)
    StoreMatchingOrchestrator().start_matching(order.id)
    offer = StoreOffer.objects.get(order=order, store=store)
    notification = StoreOfferNotification.objects.get(offer=offer)

    class UnderstandingMustNotRun:
        def understand_normalized(self, *_args, **_kwargs):
            raise AssertionError("a template reply must resume the pending seller offer")

    wake_up = parse_whatsapp_webhook({
        "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": "phone"},
            "messages": [{"id": "wamid.wakeup", "from": "237698000041", "type": "text", "text": {"body": "Bonjour"}}],
        }}]}],
    })[0]
    outcome = ConversationalOrchestrator(understanding_service=UnderstandingMustNotRun()).process_message(
        notification.conversation, wake_up
    )
    assert outcome.metadata == {"store_offer_resumed": True, "offer_id": str(offer.id)}
    assert [action.action_id for action in outcome.response.actions] == [
        f"store_offer.accept:{offer.id}", f"store_offer.reject:{offer.id}"
    ]


@pytest.mark.django_db(transaction=True)
def test_confirm_partial_accept_restarts_matching_and_only_then_creates_offer(monkeypatch):
    order, customer, products = order_with_products()
    seller = User.objects.create_user(whatsapp_phone="+237698000002")
    create_channel_identity(user=seller, channel=MessagingChannel.TELEGRAM, external_user_id="seller-partial-e2e")
    store = catalog_store("Partial E2E", [products["Riz"], products["Huile"]], owner=seller)
    confirm_order(order)

    StoreMatchingOrchestrator().start_matching(order.id)
    proposal = PartialStoreCoverageProposal.objects.get(order=order)
    assert proposal.status == PartialStoreCoverageProposalStatus.PENDING
    assert not StoreOffer.objects.filter(order=order).exists()
    monkeypatch.setattr(PartialCoverageConsentService, "_enqueue", staticmethod(lambda order_id, *_: StoreMatchingOrchestrator().start_matching(order_id)))
    PartialCoverageConsentService().accept(order_id=order.id, customer=customer)

    assert StoreOffer.objects.filter(order=order, store=store, status="PENDING").exists()


@pytest.mark.django_db(transaction=True)
def test_confirm_zero_coverage_fails_and_creates_one_customer_notification():
    order, customer, _ = order_with_products(names=("Riz",))
    confirm_order(order)

    StoreMatchingOrchestrator().start_matching(order.id)
    order.refresh_from_db()
    assert order.status == OrderStatus.FAILED
    assert OperationalNotification.objects.filter(recipient=customer, kind=NotificationKind.ORDER_NO_STORE_AVAILABLE).count() == 1
