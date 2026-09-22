import pytest
from django.contrib.gis.geos import Point
from rest_framework.test import APIClient

from apps.audit.domain.enums import AuditEventType
from apps.audit.infrastructure.models import AuditEvent
from apps.catalog.application.services import create_category, create_product
from apps.catalog.domain.enums import ProductUnit
from apps.identity.infrastructure.models import User
from apps.orders.application.services import (
    add_order_item,
    confirm_order,
    create_order_draft,
    fail_order,
    mark_in_delivery,
    set_delivery_location,
    start_store_matching,
)
from apps.orders.domain.enums import (
    OrderCancellationCode,
    OrderFailureCode,
    OrderItemResolutionStatus,
    OrderStatus,
)
from apps.orders.domain.events import (
    OrderCancelled,
    OrderConfirmed,
    OrderRetryRequested,
)
from common.events import clear_subscribers, subscribe

BONAMOUSSADI = Point(9.7357, 4.0907, srid=4326)


@pytest.fixture(autouse=True)
def clear_event_bus():
    clear_subscribers()
    yield
    clear_subscribers()


def create_user(phone: str = "+237699887766") -> User:
    return User.objects.create_user(whatsapp_phone=phone)


def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def create_catalog_product(name: str):
    category = create_category(name=f"Category {name}")
    return create_product(name=name, category=category)


@pytest.mark.django_db(transaction=True)
def test_orders_api_main_draft_to_confirm_flow():
    user = create_user()
    mayor = create_catalog_product("Huile Mayor")
    rice = create_catalog_product("Riz parfumé")
    client = auth_client(user)
    confirmed_events = []
    subscribe(OrderConfirmed, confirmed_events.append)

    response = client.post("/api/v1/orders/", {}, format="json")
    assert response.status_code == 201
    order_id = response.data["id"]
    assert response.data["status"] == OrderStatus.DRAFT
    assert response.data["reference"].startswith("ORD-")

    response = client.post(
        f"/api/v1/orders/{order_id}/items/",
        {
            "requested_name": "riz parfumé de bonne qualité",
            "quantity": "5",
            "unit": ProductUnit.KG,
            "product_id": str(rice.id),
            "product_variant_id": None,
            "resolution_status": OrderItemResolutionStatus.MATCHED,
        },
        format="json",
    )
    assert response.status_code == 201

    response = client.post(
        f"/api/v1/orders/{order_id}/items/",
        {
            "requested_name": "Mayor",
            "quantity": "2",
            "unit": ProductUnit.BOTTLE,
            "product_id": str(mayor.id),
            "resolution_status": OrderItemResolutionStatus.MATCHED,
        },
        format="json",
    )
    assert response.status_code == 201

    response = client.patch(
        f"/api/v1/orders/{order_id}/delivery-location/",
        {
            "latitude": 4.0907,
            "longitude": 9.7357,
            "address_label": "Bonamoussadi, Douala",
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["delivery_location"] == {
        "longitude": 9.7357,
        "latitude": 4.0907,
    }

    response = client.get(f"/api/v1/orders/{order_id}/")
    assert response.status_code == 200
    assert len(response.data["items"]) == 2

    response = client.post(
        f"/api/v1/orders/{order_id}/confirm/",
        {},
        format="json",
        HTTP_X_CORRELATION_ID="corr-api-confirm",
    )
    assert response.status_code == 200
    assert response.data["status"] == OrderStatus.CONFIRMED
    assert response.data["confirmed_at"] is not None
    assert AuditEvent.objects.filter(
        entity_id=order_id,
        event_type=AuditEventType.ORDER_CONFIRMED,
    ).count() == 1
    assert len(confirmed_events) == 1
    assert confirmed_events[0].correlation_id == "corr-api-confirm"


@pytest.mark.django_db
def test_orders_api_confirm_without_location_returns_business_error():
    user = create_user()
    client = auth_client(user)
    order = create_order_draft(customer=user)
    add_order_item(order=order, requested_name="Riz", quantity=5, unit=ProductUnit.KG)

    response = client.post(f"/api/v1/orders/{order.id}/confirm/", {}, format="json")

    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == OrderStatus.DRAFT


@pytest.mark.django_db
def test_orders_api_cross_user_is_not_found():
    owner = create_user("+237699887766")
    other = create_user("+237699887767")
    order = create_order_draft(customer=owner)
    client = auth_client(other)

    assert client.get(f"/api/v1/orders/{order.id}/").status_code == 404
    assert client.post(f"/api/v1/orders/{order.id}/confirm/").status_code == 404
    assert client.post(f"/api/v1/orders/{order.id}/cancel/").status_code == 404
    assert client.post(f"/api/v1/orders/{order.id}/retry/").status_code == 404
    assert client.get(f"/api/v1/orders/{order.id}/history/").status_code == 404


@pytest.mark.django_db
def test_orders_api_blocks_content_mutation_after_confirmation():
    user = create_user()
    client = auth_client(user)
    order = create_order_draft(customer=user)
    rice = create_catalog_product("Riz mutation")
    item = add_order_item(
        order=order,
        requested_name="Riz",
        quantity=5,
        unit=ProductUnit.KG,
        product=rice,
        resolution_status=OrderItemResolutionStatus.MATCHED,
    )
    set_delivery_location(order=order, point=BONAMOUSSADI)
    confirm_order(order)

    response = client.post(
        f"/api/v1/orders/{order.id}/items/",
        {"requested_name": "Mayor", "quantity": "2", "unit": ProductUnit.BOTTLE},
        format="json",
    )
    assert response.status_code == 409

    response = client.patch(
        f"/api/v1/orders/{order.id}/items/{item.id}/",
        {"quantity": "6"},
        format="json",
    )
    assert response.status_code == 409

    response = client.delete(f"/api/v1/orders/{order.id}/items/{item.id}/")
    assert response.status_code == 409

    response = client.patch(
        f"/api/v1/orders/{order.id}/delivery-location/",
        {"latitude": 4.1, "longitude": 9.7},
        format="json",
    )
    assert response.status_code == 409


@pytest.mark.django_db(transaction=True)
def test_orders_api_cancel_creates_audit_and_event():
    user = create_user()
    order = create_order_draft(customer=user)
    client = auth_client(user)
    cancelled_events = []
    subscribe(OrderCancelled, cancelled_events.append)

    response = client.post(
        f"/api/v1/orders/{order.id}/cancel/",
        {
            "code": OrderCancellationCode.CUSTOMER_REQUEST,
            "reason": "Client changed mind",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == OrderStatus.CANCELLED
    assert AuditEvent.objects.filter(
        entity_id=order.id,
        event_type=AuditEventType.ORDER_CANCELLED,
    ).exists()
    assert len(cancelled_events) == 1


@pytest.mark.django_db(transaction=True)
def test_orders_api_retry_failed_order_uses_domain_target():
    user = create_user()
    order = create_order_draft(customer=user)
    rice = create_catalog_product("Riz retry")
    add_order_item(order=order, requested_name="Riz", quantity=5, unit=ProductUnit.KG,
                   product=rice, resolution_status=OrderItemResolutionStatus.MATCHED)
    set_delivery_location(order=order, point=BONAMOUSSADI)
    order = start_store_matching(confirm_order(order))
    failed = fail_order(order, OrderFailureCode.NO_STORE_AVAILABLE)
    client = auth_client(user)
    retry_events = []
    subscribe(OrderRetryRequested, retry_events.append)

    response = client.post(
        f"/api/v1/orders/{failed.id}/retry/",
        {"target_status": OrderStatus.DELIVERED},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == OrderStatus.MATCHING_STORES
    assert len(retry_events) == 1
    assert retry_events[0].retry_target_status == OrderStatus.MATCHING_STORES


@pytest.mark.django_db
def test_orders_api_retry_non_retryable_failure_returns_conflict():
    user = create_user()
    order = create_order_draft(customer=user)
    order.status = OrderStatus.PICKED_UP
    order.save(update_fields=["status", "updated_at"])
    order = mark_in_delivery(order)
    failed = fail_order(order, OrderFailureCode.DELIVERY_FAILED)
    client = auth_client(user)

    response = client.post(f"/api/v1/orders/{failed.id}/retry/", {}, format="json")

    assert response.status_code == 409


@pytest.mark.django_db(transaction=True)
def test_orders_api_confirm_idempotence_publishes_once():
    user = create_user()
    order = create_order_draft(customer=user)
    rice = create_catalog_product("Riz idempotence")
    add_order_item(order=order, requested_name="Riz", quantity=5, unit=ProductUnit.KG,
                   product=rice, resolution_status=OrderItemResolutionStatus.MATCHED)
    set_delivery_location(order=order, point=BONAMOUSSADI)
    client = auth_client(user)
    confirmed_events = []
    subscribe(OrderConfirmed, confirmed_events.append)

    first = client.post(f"/api/v1/orders/{order.id}/confirm/", {}, format="json")
    second = client.post(f"/api/v1/orders/{order.id}/confirm/", {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert AuditEvent.objects.filter(
        entity_id=order.id,
        event_type=AuditEventType.ORDER_CONFIRMED,
    ).count() == 1
    assert len(confirmed_events) == 1


@pytest.mark.django_db
def test_orders_api_history_is_readonly_and_scoped():
    user = create_user()
    order = create_order_draft(customer=user)
    client = auth_client(user)

    response = client.get(f"/api/v1/orders/{order.id}/history/")

    assert response.status_code == 200
    assert response.data["results"][0]["event_type"] == AuditEventType.ORDER_CREATED
    assert client.post(f"/api/v1/orders/{order.id}/history/").status_code == 405
