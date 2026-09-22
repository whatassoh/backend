import pytest
from types import SimpleNamespace
from django.contrib.gis.geos import Point

from apps.catalog.application.services import add_product_to_store, create_category, create_product
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.identity.infrastructure.models import User
from apps.orders.application.services import add_order_item, confirm_order, create_order_draft, set_delivery_location
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.orders.infrastructure.models import OrderItem
from apps.stores.application.matching import StoreMatchingOrchestrator
from apps.stores.application.partial_coverage import PartialCoverageConsentService
from apps.stores.application.services import create_store
from apps.stores.domain.enums import PartialStoreCoverageProposalStatus
from apps.stores.infrastructure.models import PartialStoreCoverageProposal, StoreOffer
from common.events import clear_subscribers


@pytest.fixture(autouse=True)
def isolate_event_bus():
    """This module invokes the orchestrator explicitly, not via Celery."""
    clear_subscribers()
    yield
    clear_subscribers()
    # Restore the production bridge for tests collected after this module.
    from apps.orders.domain.events import OrderConfirmed
    from apps.stores.application.order_confirmed import enqueue_store_matching
    from common.events import subscribe
    subscribe(OrderConfirmed, enqueue_store_matching)


def setup_order(*, items=("Riz", "Huile", "Gaz")):
    user = User.objects.create_user(whatsapp_phone=f"+237690{User.objects.count():06d}")
    order = create_order_draft(customer=user)
    category = create_category(name=f"Food partial {order.id}")
    products = {name: create_product(name=f"{name} {order.id}", category=category) for name in items}
    rows = {}
    for name, product in products.items():
        rows[name] = add_order_item(order=order, requested_name=name, quantity=1, unit=ProductUnit.UNIT,
            product=product, resolution_status=OrderItemResolutionStatus.MATCHED)
    set_delivery_location(order, Point(9.7357, 4.0907, srid=4326))
    return confirm_order(order), user, products, rows


def store(name, longitude, products):
    value = create_store(name=name, city="Douala", location=Point(longitude, 4.0907, srid=4326))
    for product in products:
        add_product_to_store(store=value, product=product, availability_status=ProductAvailability.AVAILABLE)
    return value


@pytest.mark.django_db
def test_partial_proposal_is_best_coverage_and_never_creates_store_offer(monkeypatch):
    order, _, products, rows = setup_order()
    near_partial = store("Near partial", 9.736, [products["Riz"], products["Huile"]])
    store("Far one item", 9.737, [products["Riz"]])
    monkeypatch.setattr(StoreMatchingOrchestrator, "_notify_partial_proposal", staticmethod(lambda *_: None))

    StoreMatchingOrchestrator().start_matching(order.id)

    order.refresh_from_db()
    proposal = PartialStoreCoverageProposal.objects.get(order=order)
    assert order.status == OrderStatus.AWAITING_PARTIAL_COVERAGE
    assert proposal.store_id == near_partial.id
    assert [row["order_item_id"] for row in proposal.proposal_snapshot["uncovered_items"]] == [str(rows["Gaz"].id)]
    assert StoreOffer.objects.filter(order=order).count() == 0


@pytest.mark.django_db
def test_full_coverage_always_beats_closer_partial(monkeypatch):
    order, _, products, _ = setup_order()
    store("Closest partial", 9.736, [products["Riz"], products["Huile"]])
    full = store("Far full", 9.740, list(products.values()))
    monkeypatch.setattr(StoreMatchingOrchestrator, "_notify_partial_proposal", staticmethod(lambda *_: None))

    StoreMatchingOrchestrator(
        notifications=SimpleNamespace(create_for_offer=lambda *_args, **_kwargs: [object()])
    ).start_matching(order.id)

    order.refresh_from_db()
    assert order.status == OrderStatus.STORE_OFFERED
    assert StoreOffer.objects.get(order=order).store_id == full.id
    assert not PartialStoreCoverageProposal.objects.filter(order=order).exists()


@pytest.mark.django_db
def test_accept_removes_only_persisted_uncovered_items_and_requeues(monkeypatch):
    order, user, products, rows = setup_order()
    store("Partial", 9.736, [products["Riz"], products["Huile"]])
    monkeypatch.setattr(StoreMatchingOrchestrator, "_notify_partial_proposal", staticmethod(lambda *_: None))
    calls = []
    commit_callbacks = []
    monkeypatch.setattr(PartialCoverageConsentService, "_enqueue", staticmethod(lambda *args: calls.append(args)))
    monkeypatch.setattr("apps.stores.application.partial_coverage.transaction.on_commit", commit_callbacks.append)
    StoreMatchingOrchestrator().start_matching(order.id)
    # The first callback belongs to the customer-proposal notification.
    commit_callbacks.clear()

    result, replay = PartialCoverageConsentService().accept(order_id=order.id, customer=user)

    assert not replay and result.status == OrderStatus.CONFIRMED
    assert set(OrderItem.objects.filter(order=order).values_list("id", flat=True)) == {rows["Riz"].id, rows["Huile"].id}
    proposal = PartialStoreCoverageProposal.objects.get(order=order)
    assert proposal.status == PartialStoreCoverageProposalStatus.ACCEPTED
    assert len(commit_callbacks) == 1
    commit_callbacks[0]()
    assert calls and calls[0][0] == order.id
    _, replay = PartialCoverageConsentService().accept(order_id=order.id, customer=user)
    assert replay


@pytest.mark.django_db
def test_forged_customer_cannot_consume_or_remove_items(monkeypatch):
    order, _, products, rows = setup_order()
    other = User.objects.create_user(whatsapp_phone="+237699998888")
    store("Partial forged", 9.736, [products["Riz"], products["Huile"]])
    monkeypatch.setattr(StoreMatchingOrchestrator, "_notify_partial_proposal", staticmethod(lambda *_: None))
    StoreMatchingOrchestrator().start_matching(order.id)

    with pytest.raises(PermissionError):
        PartialCoverageConsentService().accept(order_id=order.id, customer=other)
    assert OrderItem.objects.filter(order=order).count() == 3


@pytest.mark.django_db
def test_zero_coverage_exhausts_without_partial_proposal(monkeypatch):
    order, _, products, _ = setup_order(items=("Riz",))
    store("No rice", 9.736, [])
    monkeypatch.setattr(StoreMatchingOrchestrator, "_notify_partial_proposal", staticmethod(lambda *_: None))

    StoreMatchingOrchestrator().start_matching(order.id)

    order.refresh_from_db()
    assert order.status == OrderStatus.FAILED
    assert not PartialStoreCoverageProposal.objects.filter(order=order).exists()
