from types import SimpleNamespace

import pytest
from django.contrib.gis.geos import Point
from django.db import transaction

from apps.catalog.application.services import create_category, create_product
from apps.catalog.domain.enums import ProductUnit
from apps.identity.infrastructure.models import User
from apps.orders.application.services import add_order_item, confirm_order, create_order_draft, set_delivery_location
from apps.orders.domain.enums import OrderItemResolutionStatus, OrderStatus
from apps.orders.domain.events import OrderConfirmed
from apps.stores.application.order_confirmed import enqueue_store_matching
from apps.stores.tasks import start_store_matching_for_order
from common.events import clear_subscribers, subscribe


def confirmable_order():
    user = User.objects.create_user(whatsapp_phone=f"+237690{User.objects.count():06d}")
    order = create_order_draft(customer=user)
    product = create_product(name=f"Rice {order.id}", category=create_category(name=f"Food {order.id}"))
    add_order_item(order=order, requested_name="Rice", quantity=1, unit=ProductUnit.KG, product=product, resolution_status=OrderItemResolutionStatus.MATCHED)
    set_delivery_location(order=order, point=Point(9.7, 4.05, srid=4326))
    return order


@pytest.fixture(autouse=True)
def isolated_event_bus():
    clear_subscribers()
    yield
    clear_subscribers()


@pytest.mark.django_db(transaction=True)
def test_confirmed_event_enqueues_scalar_order_id_only_after_commit(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "apps.stores.tasks.start_store_matching_for_order.delay",
        lambda *args: calls.append(args),
    )
    subscribe(OrderConfirmed, enqueue_store_matching)
    order = confirmable_order()

    with transaction.atomic():
        confirmed = confirm_order(order, correlation_id="corr-match")
        assert confirmed.status == OrderStatus.CONFIRMED
        assert calls == []

    assert calls == [(str(order.id), "corr-match")]


@pytest.mark.django_db(transaction=True)
def test_confirm_rollback_enqueues_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr("apps.stores.tasks.start_store_matching_for_order.delay", lambda *args: calls.append(args))
    subscribe(OrderConfirmed, enqueue_store_matching)
    order = confirmable_order()

    with pytest.raises(RuntimeError), transaction.atomic():
        confirm_order(order)
        raise RuntimeError("rollback")

    assert calls == []


@pytest.mark.django_db
def test_task_invokes_matching_once_and_non_confirmed_orders_are_noops(monkeypatch):
    order = confirmable_order()
    calls = []

    class Matching:
        def start_matching(self, order_id, correlation_id=None):
            calls.append((order_id, correlation_id))
            return SimpleNamespace(id="attempt-1")

    monkeypatch.setattr("apps.stores.application.matching.StoreMatchingOrchestrator", Matching)
    result = start_store_matching_for_order.run(str(order.id), "corr-task")
    assert result["status"] == "NOOP"
    assert calls == []

    confirm_order(order)
    result = start_store_matching_for_order.run(str(order.id), "corr-task")
    assert result["status"] == "STARTED"
    assert calls == [(order.id, "corr-task")]
