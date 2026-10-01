from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.gis.geos import Point
from django.test import override_settings
from django.utils import timezone

from apps.ai.domain.understanding import (
    ExtractedItem,
    MessageIntent,
    UserMessageUnderstanding,
)
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.responding import send_response_plan
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import ActiveJourney, ConversationContext
from apps.drivers.application.delivery_execution import encode_delivery_action
from apps.drivers.application.offers import encode_driver_offer_action
from apps.drivers.domain.enums import (
    DriverAvailability,
    DriverOfferStatus,
    DriverStatus,
    VehicleType,
)
from apps.drivers.infrastructure.models import (
    DriverAssignment,
    DriverOffer,
    DriverProfile,
)
from apps.identity.domain.enums import UserRoleType
from apps.identity.infrastructure.models import User, UserRole
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import (
    StoreMembershipRole,
    StoreOfferStatus,
)
from apps.stores.infrastructure.models import Store, StoreMembership, StoreOffer
from apps.telegram.adapters.inbound import parse_telegram_update
from apps.whatsapp.adapters.inbound import parse_whatsapp_webhook


class UnderstandingQueue:
    def __init__(self, *values):
        self.values = list(values)

    def understand_normalized(self, *_args, **_kwargs):
        return self.values.pop(0)


class NoUnderstanding:
    def understand_normalized(self, *_args, **_kwargs):
        raise AssertionError("Driver Workspace must resolve this turn deterministically")


def conversation(channel=MessagingChannel.WHATSAPP, external_id=None):
    user = User.objects.create_user(whatsapp_phone=f"+23764{User.objects.count():07d}")
    external_id = external_id or f"driver-workspace-{user.id}"
    identity = create_channel_identity(
        user=user,
        channel=channel,
        external_user_id=str(external_id),
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


def driver_conversation(
    *,
    channel=MessagingChannel.WHATSAPP,
    availability=DriverAvailability.OFFLINE,
    location_at=None,
    external_id=None,
):
    convo = conversation(channel, external_id)
    driver = DriverProfile.objects.create(
        user=convo.user,
        status=DriverStatus.ACTIVE,
        availability_status=availability,
        vehicle_type=VehicleType.MOTORBIKE,
        last_location=(Point(9.7, 4.05, srid=4326) if location_at else None),
        last_location_at=location_at,
    )
    UserRole.objects.get_or_create(user=convo.user, role=UserRoleType.DRIVER)
    return convo, driver


def inbound(
    message_id,
    *,
    text=None,
    action_id=None,
    location=None,
    channel=MessagingChannel.WHATSAPP,
):
    return NormalizedInboundMessage(
        channel=channel,
        external_message_id=message_id,
        external_user_id="driver",
        message_type=(
            MessageType.INTERACTIVE
            if action_id
            else MessageType.LOCATION
            if location
            else MessageType.TEXT
        ),
        text=text,
        location=location,
        metadata={"action_id": action_id} if action_id else {},
    )


def app(*understandings):
    return ConversationalOrchestrator(
        understanding_service=(
            UnderstandingQueue(*understandings) if understandings else NoUnderstanding()
        )
    )


def delivery_assignment(driver, status):
    customer = User.objects.create_user(
        whatsapp_phone=f"+23763{User.objects.count():07d}"
    )
    order = Order.objects.create(
        reference="ORD-261001-ABCD",
        customer=customer,
        status=status,
    )
    store = Store.objects.create(
        name="Pickup Store",
        city="Douala",
        location=Point(9.71, 4.06, srid=4326),
    )
    StoreOffer.objects.create(
        order=order,
        store=store,
        status=StoreOfferStatus.ACCEPTED,
        rank=1,
        strategy_version="test",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    offer = DriverOffer.objects.create(
        order=order,
        driver=driver,
        status=DriverOfferStatus.ACCEPTED,
        rank=1,
        strategy_version="test",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assignment = DriverAssignment.objects.create(
        order=order,
        driver=driver,
        offer=offer,
        is_active=status != OrderStatus.DELIVERED,
    )
    return order, assignment


@pytest.mark.django_db
def test_existing_driver_opens_workspace_without_restarting_onboarding():
    convo, _driver = driver_conversation()
    understanding = UserMessageUnderstanding(
        intent=MessageIntent.DELIVER,
        confidence=Decimal(".99"),
    )

    outcome = app(understanding).process_message(
        convo, inbound("existing", text="Je veux faire des livraisons")
    )

    assert outcome.metadata["driver_workspace"] is True
    assert outcome.metadata["driver_workspace_state"] == "UNAVAILABLE"
    assert DriverProfile.objects.filter(user=convo.user).count() == 1
    convo.refresh_from_db()
    assert ConversationContext.from_payload(convo.context).driver_onboarding is None


@pytest.mark.django_db
def test_unavailable_driver_is_offered_real_availability_action():
    convo, _driver = driver_conversation()

    outcome = app().process_message(
        convo, inbound("next-offline", text="C'est quoi la suite ?")
    )

    assert outcome.metadata["driver_workspace_state"] == "UNAVAILABLE"
    assert outcome.response.actions[0].action_id == "driver.availability.available"


@pytest.mark.django_db
def test_availability_callback_recomputes_matchability_idempotently():
    convo, driver = driver_conversation()

    outcome = app().process_message(
        convo,
        inbound("available", action_id="driver.availability.available"),
    )

    driver.refresh_from_db()
    assert driver.availability_status == DriverAvailability.AVAILABLE
    assert outcome.metadata["driver_workspace_state"] == "LOCATION_MISSING"
    assert outcome.response.request_location is True


@pytest.mark.django_db
def test_available_driver_without_location_is_not_matchable():
    convo, _driver = driver_conversation(availability=DriverAvailability.AVAILABLE)

    outcome = app().process_message(
        convo, inbound("next-no-location", text="Que puis-je faire maintenant ?")
    )

    assert outcome.metadata["matchability"] == "LOCATION_REQUIRED"
    assert outcome.metadata["driver_workspace_state"] == "LOCATION_MISSING"
    assert outcome.response.request_location is True


@pytest.mark.django_db
@override_settings(DRIVER_LOCATION_FRESHNESS_SECONDS=120)
def test_available_driver_with_stale_location_must_refresh_it():
    convo, _driver = driver_conversation(
        availability=DriverAvailability.AVAILABLE,
        location_at=timezone.now() - timedelta(seconds=121),
    )

    outcome = app().process_message(
        convo, inbound("next-stale", text="Quel est mon statut ?")
    )

    assert outcome.metadata["matchability"] == "LOCATION_REFRESH_REQUIRED"
    assert outcome.metadata["driver_workspace_state"] == "LOCATION_STALE"
    assert outcome.response.request_location is True


@pytest.mark.django_db
def test_available_driver_with_fresh_location_is_matchable_and_waiting():
    convo, _driver = driver_conversation(
        availability=DriverAvailability.AVAILABLE,
        location_at=timezone.now(),
    )

    outcome = app().process_message(
        convo, inbound("next-ready", text="Je suis prêt, je fais quoi ?")
    )

    assert outcome.metadata["matchability"] == "MATCHABLE"
    assert outcome.metadata["driver_workspace_state"] == "MATCHABLE"
    assert "prêt à recevoir" in outcome.response.text


@pytest.mark.django_db
def test_pending_driver_offer_takes_priority_and_reuses_existing_callbacks():
    convo, driver = driver_conversation(
        availability=DriverAvailability.AVAILABLE,
        location_at=timezone.now(),
    )
    customer = User.objects.create_user(whatsapp_phone="+237620000001")
    order = Order.objects.create(
        reference="ORD-261001-OFFX",
        customer=customer,
        status=OrderStatus.MATCHING_DRIVERS,
    )
    offer = DriverOffer.objects.create(
        order=order,
        driver=driver,
        rank=1,
        strategy_version="test",
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    outcome = app().process_message(
        convo, inbound("next-offer", text="C'est quoi la suite en tant que livreur ?")
    )

    assert outcome.metadata["driver_workspace_state"] == "PENDING_OFFER"
    assert [action.action_id for action in outcome.response.actions] == [
        encode_driver_offer_action("driver_offer.accept", offer.id),
        encode_driver_offer_action("driver_offer.reject", offer.id),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("order_status", "expected_action"),
    [
        (OrderStatus.DRIVER_ASSIGNED, "delivery.confirm_pickup"),
        (OrderStatus.PICKED_UP, "delivery.start_delivery"),
        (OrderStatus.IN_DELIVERY, "delivery.confirm_delivered"),
    ],
)
def test_active_assignment_presents_existing_driver_2_action(
    order_status, expected_action
):
    convo, driver = driver_conversation(
        availability=DriverAvailability.AVAILABLE,
        location_at=timezone.now(),
    )
    _order, assignment = delivery_assignment(driver, order_status)

    outcome = app().process_message(
        convo, inbound(f"mission-{order_status}", text="Que dois-je faire ?")
    )

    assert outcome.metadata["driver_workspace_state"] == "ACTIVE_ASSIGNMENT"
    assert outcome.response.actions[0].action_id == encode_delivery_action(
        expected_action, assignment.id
    )


@pytest.mark.django_db
def test_delivered_inactive_assignment_returns_to_matchability_state():
    convo, driver = driver_conversation(
        availability=DriverAvailability.AVAILABLE,
        location_at=timezone.now(),
    )
    delivery_assignment(driver, OrderStatus.DELIVERED)

    outcome = app().process_message(
        convo, inbound("after-delivered", text="Je fais quoi maintenant ?")
    )

    assert outcome.metadata["driver_workspace_state"] == "MATCHABLE"


@pytest.mark.django_db
def test_driver_2_completion_hands_back_to_workspace():
    convo, driver = driver_conversation(
        availability=DriverAvailability.AVAILABLE,
        location_at=timezone.now(),
    )
    _order, assignment = delivery_assignment(driver, OrderStatus.IN_DELIVERY)
    convo.context = ConversationContext(active_journey=ActiveJourney.DELIVER).to_payload()
    convo.save(update_fields=["context", "updated_at"])

    outcome = app().process_message(
        convo,
        inbound(
            "complete-to-workspace",
            action_id=encode_delivery_action(
                "delivery.confirm_delivered", assignment.id
            ),
        ),
    )

    assignment.refresh_from_db()
    assert assignment.is_active is False
    assert outcome.metadata["delivery_completion"] == "CONFIRMED"
    assert outcome.metadata["driver_workspace_state"] == "MATCHABLE"


@pytest.mark.django_db
@pytest.mark.parametrize("channel", [MessagingChannel.WHATSAPP, MessagingChannel.TELEGRAM])
def test_channel_location_payload_recomputes_workspace_and_builds_outbound(
    channel, monkeypatch
):
    external_id = "237699887766" if channel == MessagingChannel.WHATSAPP else "123456"
    convo, _driver = driver_conversation(
        channel=channel,
        availability=DriverAvailability.AVAILABLE,
        location_at=timezone.now() - timedelta(days=1),
        external_id=external_id,
    )
    convo.context = ConversationContext(active_journey=ActiveJourney.DELIVER).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    timestamp = str(int(timezone.now().timestamp()))
    if channel == MessagingChannel.WHATSAPP:
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "metadata": {"phone_number_id": "phone-id"},
                "messages": [{
                    "from": external_id,
                    "id": "wamid.driver-location",
                    "timestamp": timestamp,
                    "type": "location",
                    "location": {"latitude": 4.05, "longitude": 9.7},
                }],
            }}]}],
        }
        message = parse_whatsapp_webhook(payload)[0]
    else:
        payload = {
            "update_id": 9001,
            "message": {
                "message_id": 101,
                "date": int(timestamp),
                "from": {"id": 123456, "is_bot": False, "first_name": "Jacob"},
                "chat": {"id": 123456, "type": "private"},
                "location": {"latitude": 4.05, "longitude": 9.7},
            },
        }
        message = parse_telegram_update(payload)[0]

    outcome = app().process_message(convo, message)

    captured = {}
    if channel == MessagingChannel.WHATSAPP:
        def capture_whatsapp(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(message_event=None)

        monkeypatch.setattr(
            "apps.whatsapp.application.outbound.send_whatsapp_interactive_reply_buttons",
            capture_whatsapp,
        )
    else:
        def capture_telegram(message):
            captured["message"] = message
            return SimpleNamespace(message_event=None)

        monkeypatch.setattr(
            "apps.messaging.application.orchestration.send", capture_telegram
        )
        monkeypatch.setattr(
            "apps.conversations.application.services.record_outbound_message",
            lambda **_kwargs: None,
        )
    send_response_plan(conversation=convo, response=outcome.response)

    assert outcome.metadata["driver_location"] is True
    assert outcome.metadata["driver_workspace_state"] == "MATCHABLE"
    assert outcome.response.actions
    if channel == MessagingChannel.WHATSAPP:
        assert captured["buttons"][0].action_id == "driver.availability.offline"
    else:
        assert captured["message"].metadata["actions"][0]["id"] == (
            "driver.availability.offline"
        )


@pytest.mark.django_db
def test_multi_role_driver_purchase_switches_to_buy():
    convo, _driver = driver_conversation()
    UserRole.objects.get_or_create(user=convo.user, role=UserRoleType.CLIENT)
    convo.context = ConversationContext(active_journey=ActiveJourney.DELIVER).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    understanding = UserMessageUnderstanding(
        intent=MessageIntent.CREATE_OR_UPDATE_ORDER,
        confidence=Decimal(".99"),
        items=(ExtractedItem(requested_name="riz", quantity=Decimal("1")),),
    )

    outcome = app(understanding).process_message(
        convo, inbound("driver-buy", text="Je veux acheter du riz")
    )

    convo.refresh_from_db()
    assert ConversationContext.from_payload(convo.context).active_journey == ActiveJourney.BUY
    assert outcome.metadata.get("driver_workspace") is not True


@pytest.mark.django_db
def test_multi_role_driver_store_request_routes_to_seller_workspace():
    convo, _driver = driver_conversation()
    store = Store.objects.create(
        name="Driver Seller Store",
        city="Douala",
        location=Point(9.7, 4.05, srid=4326),
    )
    StoreMembership.objects.create(
        store=store,
        user=convo.user,
        role=StoreMembershipRole.OWNER,
    )

    outcome = app().process_message(
        convo, inbound("driver-sell", text="Je veux gérer ma boutique")
    )

    assert outcome.metadata["seller_workspace"] is True
    assert outcome.metadata.get("driver_workspace") is not True


@pytest.mark.django_db
def test_client_seller_driver_explicit_delivery_opens_driver_workspace():
    convo, _driver = driver_conversation()
    UserRole.objects.get_or_create(user=convo.user, role=UserRoleType.CLIENT)
    UserRole.objects.get_or_create(user=convo.user, role=UserRoleType.STORE_OWNER)
    understanding = UserMessageUnderstanding(
        intent=MessageIntent.DELIVER,
        confidence=Decimal(".99"),
    )

    outcome = app(understanding).process_message(
        convo, inbound("explicit-driver", text="Je veux faire des livraisons")
    )

    assert outcome.metadata["driver_workspace"] is True
    assert outcome.metadata["driver_workspace_state"] == "UNAVAILABLE"


@pytest.mark.django_db
def test_old_onboarding_confirm_is_idempotent_for_existing_profile():
    convo, _driver = driver_conversation()

    outcome = app().process_message(
        convo,
        inbound("old-driver-confirm", action_id="driver_onboarding.confirm"),
    )

    assert outcome.metadata["driver_workspace"] is True
    assert DriverProfile.objects.filter(user=convo.user).count() == 1


@pytest.mark.django_db
def test_forged_assignment_callback_is_rejected_by_driver_2_authorization():
    convo, _driver = driver_conversation()
    other_convo, other_driver = driver_conversation(
        availability=DriverAvailability.AVAILABLE,
        location_at=timezone.now(),
    )
    _order, assignment = delivery_assignment(
        other_driver, OrderStatus.DRIVER_ASSIGNED
    )

    outcome = app().process_message(
        convo,
        inbound(
            "forged-assignment",
            action_id=encode_delivery_action(
                "delivery.confirm_pickup", assignment.id
            ),
        ),
    )

    assert outcome.status == "REJECTED"
    assignment.refresh_from_db()
    assert assignment.is_active is True
    assert other_convo.user_id != convo.user_id
