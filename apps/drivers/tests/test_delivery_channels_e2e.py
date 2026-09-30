"""Real inbound-adapter E2E coverage for the driver delivery happy path."""

from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.drivers.application.dispatch import DriverMatchingOrchestrator
from apps.drivers.application.matching import (
    DriverMatchingResult,
    DriverMatchingStatus,
    RankedDriverCandidate,
)
from apps.drivers.application.offers import encode_driver_offer_action
from apps.drivers.application.services import (
    activate_driver,
    create_driver_profile,
    set_driver_available,
)
from apps.drivers.domain.enums import DriverMatchingAttemptStatus, DriverOfferStatus
from apps.drivers.infrastructure.models import DriverAssignment, DriverOffer
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import ProviderSendResult
from apps.messaging.domain.enums import MessagingChannel
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order
from apps.stores.domain.enums import StoreOfferStatus
from apps.stores.infrastructure.models import Store, StoreOffer
from apps.telegram.application.services import process_telegram_webhook
from apps.whatsapp.application.services import process_whatsapp_webhook


class FixedDriverMatching:
    strategy_version = "DRIVER_2_E2E"

    def __init__(self, *drivers):
        self.drivers = drivers

    def find_candidates(self, order):
        return DriverMatchingResult(
            order_id=str(order.id),
            pickup_store_id=None,
            status=DriverMatchingStatus.OK,
            candidates=tuple(
                RankedDriverCandidate(
                    driver_id=str(driver.id),
                    rank=rank,
                    distance_meters=25 * rank,
                    matchability="MATCHABLE",
                    location_age_seconds=0,
                )
                for rank, driver in enumerate(self.drivers, start=1)
            ),
        )


def _setup(channel):
    suffix = User.objects.count()
    customer = User.objects.create_user(whatsapp_phone=f"+237670{suffix:06d}")
    driver_user = User.objects.create_user(whatsapp_phone=f"+237671{suffix:06d}")
    driver = activate_driver(create_driver_profile(driver_user))
    driver.last_location = Point(9.70, 4.05, srid=4326)
    driver.last_location_at = timezone.now()
    driver.save(update_fields=["last_location", "last_location_at", "updated_at"])
    set_driver_available(driver)
    external_id = "771001" if channel == MessagingChannel.TELEGRAM else "237671001001"
    identity = create_channel_identity(
        user=driver_user,
        channel=channel,
        external_user_id=external_id,
    )
    store = Store.objects.create(
        name="Boutique Driver E2E",
        city="Douala",
        address_label="Akwa, Rue Joss",
        location=Point(9.70, 4.05, srid=4326),
    )
    order = Order.objects.create(
        reference=f"D2-{Order.objects.count():012d}"[-15:],
        customer=customer,
        status=OrderStatus.READY_FOR_PICKUP,
        delivery_location=Point(9.7357, 4.0907, srid=4326),
        delivery_address_label="Bonamoussadi, rond-point",
    )
    StoreOffer.objects.create(
        order=order,
        store=store,
        status=StoreOfferStatus.ACCEPTED,
        rank=1,
        strategy_version="TEST",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    DriverMatchingOrchestrator(
        matching_service=FixedDriverMatching(driver)
    ).start_matching(order.id)
    return order, driver, identity, DriverOffer.objects.get(order=order)


def _telegram_callback(*, sequence, external_id, action_id):
    return {
        "update_id": 9000 + sequence,
        "callback_query": {
            "id": f"driver-e2e-{sequence}",
            "from": {"id": int(external_id), "first_name": "Driver"},
            "message": {"message_id": sequence, "chat": {"id": int(external_id)}},
            "data": action_id,
        },
    }


def _whatsapp_callback(*, sequence, external_id, action_id):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-driver-e2e",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "test"},
                            "messages": [
                                {
                                    "from": external_id,
                                    "id": f"wamid.driver-e2e-{sequence}",
                                    "timestamp": str(int(timezone.now().timestamp())),
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": action_id,
                                            "title": "Action",
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


@pytest.mark.django_db
@pytest.mark.parametrize(
    "channel", [MessagingChannel.TELEGRAM, MessagingChannel.WHATSAPP]
)
def test_driver_offer_to_delivered_through_real_channel_callbacks(channel, monkeypatch):
    monkeypatch.setattr(
        "apps.drivers.tasks.deliver_driver_offer_notification.delay",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "apps.drivers.tasks.expire_driver_offer.apply_async",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "apps.messaging.tasks.deliver_operational_notification.delay",
        lambda *_args: None,
    )
    sent = []

    if channel == MessagingChannel.TELEGRAM:

        class Provider:
            from apps.messaging.domain.providers import ProviderCapabilities

            capabilities = ProviderCapabilities(text=True, interactive=True)

            def send(self, message):
                sent.append(message)
                return ProviderSendResult(channel=channel)

        monkeypatch.setattr(
            "apps.messaging.application.orchestration.get_provider",
            lambda _channel: Provider(),
        )
    else:
        monkeypatch.setattr(
            "apps.whatsapp.domain.outbound_policy.WhatsAppOutboundPolicy.decide",
            lambda *_args: "FREE_FORM_ALLOWED",
        )
        monkeypatch.setattr(
            "apps.whatsapp.application.outbound.send_whatsapp_interactive_reply_buttons",
            lambda **kwargs: sent.append(kwargs) or ProviderSendResult(channel=channel),
        )

    order, driver, identity, offer = _setup(channel)

    def submit(sequence, action_id):
        payload = (
            _telegram_callback(
                sequence=sequence,
                external_id=identity.external_user_id,
                action_id=action_id,
            )
            if channel == MessagingChannel.TELEGRAM
            else _whatsapp_callback(
                sequence=sequence,
                external_id=identity.external_user_id,
                action_id=action_id,
            )
        )
        processor = (
            process_telegram_webhook
            if channel == MessagingChannel.TELEGRAM
            else process_whatsapp_webhook
        )
        assert processor(payload)["processed"] == 1

    submit(1, encode_driver_offer_action("driver_offer.accept", offer.id))
    order.refresh_from_db()
    offer.refresh_from_db()
    assignment = DriverAssignment.objects.get(order=order)
    assert offer.status == DriverOfferStatus.ACCEPTED
    assert order.status == OrderStatus.DRIVER_ASSIGNED
    assert order.driver_matching_attempt.status == DriverMatchingAttemptStatus.SUCCEEDED
    pickup_action = (
        sent[-1].metadata["actions"][0]["id"]
        if channel == MessagingChannel.TELEGRAM
        else sent[-1]["buttons"][0].action_id
    )
    assert pickup_action == f"delivery.confirm_pickup:{assignment.id}"

    submit(2, pickup_action)
    order.refresh_from_db()
    assert order.status == OrderStatus.PICKED_UP
    start_action = (
        sent[-1].metadata["actions"][0]["id"]
        if channel == MessagingChannel.TELEGRAM
        else sent[-1]["buttons"][0].action_id
    )
    assert start_action == f"delivery.start_delivery:{assignment.id}"

    submit(3, start_action)
    order.refresh_from_db()
    assert order.status == OrderStatus.IN_DELIVERY
    delivered_action = (
        sent[-1].metadata["actions"][0]["id"]
        if channel == MessagingChannel.TELEGRAM
        else sent[-1]["buttons"][0].action_id
    )
    assert delivered_action == f"delivery.confirm_delivered:{assignment.id}"

    submit(4, delivered_action)
    order.refresh_from_db()
    assignment.refresh_from_db()
    assert order.status == OrderStatus.DELIVERED
    assert assignment.is_active is False


@pytest.mark.django_db
def test_telegram_refuse_advances_to_next_driver_then_accepts(monkeypatch):
    monkeypatch.setattr(
        "apps.drivers.tasks.deliver_driver_offer_notification.delay",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "apps.drivers.tasks.expire_driver_offer.apply_async",
        lambda *_args, **_kwargs: None,
    )

    order, first, first_identity, first_offer = _setup(MessagingChannel.TELEGRAM)
    second_user = User.objects.create_user(whatsapp_phone="+237671999999")
    second = activate_driver(create_driver_profile(second_user))
    second.last_location = Point(9.70, 4.05, srid=4326)
    second.last_location_at = timezone.now()
    second.save(update_fields=["last_location", "last_location_at", "updated_at"])
    set_driver_available(second)
    second_identity = create_channel_identity(
        user=second_user,
        channel=MessagingChannel.TELEGRAM,
        external_user_id="771002",
    )

    # Rebuild only the persisted candidate snapshot to model the original
    # two-driver matching result without bypassing offer action handling.
    attempt = order.driver_matching_attempt
    attempt.candidate_snapshot.append(
        {
            "driver_id": str(second.id),
            "rank": 2,
            "distance_meters": 50,
            "strategy_version": FixedDriverMatching.strategy_version,
        }
    )
    attempt.save(update_fields=["candidate_snapshot", "updated_at"])

    provider_messages = []

    class Provider:
        from apps.messaging.domain.providers import ProviderCapabilities

        capabilities = ProviderCapabilities(text=True, interactive=True)

        def send(self, message):
            provider_messages.append(message)
            return ProviderSendResult(channel=MessagingChannel.TELEGRAM)

    monkeypatch.setattr(
        "apps.messaging.application.orchestration.get_provider",
        lambda _channel: Provider(),
    )
    assert (
        process_telegram_webhook(
            _telegram_callback(
                sequence=20,
                external_id=first_identity.external_user_id,
                action_id=encode_driver_offer_action(
                    "driver_offer.reject", first_offer.id
                ),
            )
        )["processed"]
        == 1
    )
    second_offer = DriverOffer.objects.get(order=order, driver=second)
    first_offer.refresh_from_db()
    assert first_offer.status == DriverOfferStatus.REJECTED
    assert second_offer.status == DriverOfferStatus.PENDING

    assert (
        process_telegram_webhook(
            _telegram_callback(
                sequence=21,
                external_id=second_identity.external_user_id,
                action_id=encode_driver_offer_action(
                    "driver_offer.accept", second_offer.id
                ),
            )
        )["processed"]
        == 1
    )
    order.refresh_from_db()
    second_offer.refresh_from_db()
    assert second_offer.status == DriverOfferStatus.ACCEPTED
    assert order.status == OrderStatus.DRIVER_ASSIGNED
    assert order.driver_assignment.driver_id == second.id
