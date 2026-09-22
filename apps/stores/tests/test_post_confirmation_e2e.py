"""Post-confirmation business E2E tests; provider delivery is deliberately mocked."""
import pytest
from django.contrib.gis.geos import Point

from apps.catalog.application.services import add_product_to_store, create_category, create_product
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel, NotificationKind
from apps.messaging.infrastructure.models import OperationalNotification
from apps.orders.application.services import add_order_item, confirm_order, create_order_draft, set_delivery_location
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.stores.application.matching import StoreMatchingOrchestrator
from apps.stores.application.partial_coverage import PartialCoverageConsentService
from apps.stores.application.services import create_store
from apps.stores.domain.enums import PartialStoreCoverageProposalStatus
from apps.stores.infrastructure.models import PartialStoreCoverageProposal, StoreOffer
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
