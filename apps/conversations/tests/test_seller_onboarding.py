import threading

import pytest
from django.contrib.gis.geos import Point

from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.seller_onboarding import SellerOnboardingService
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import (
    ActiveJourney,
    ConversationContext,
    SellerOnboardingStage,
)
from apps.identity.domain.enums import UserRoleType
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import MessageLocation, NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.stores.domain.enums import StoreMembershipRole
from apps.stores.infrastructure.models import Store, StoreMembership


class NoLLM:
    def understand_normalized(self, *_args, **_kwargs):
        raise AssertionError("seller onboarding inputs must not create a Store through the LLM")


def conversation(channel=MessagingChannel.WHATSAPP):
    user = User.objects.create_user(whatsapp_phone=f"+23767{User.objects.count():07d}")
    identity = create_channel_identity(
        user=user, channel=channel, external_user_id=f"seller-{user.id}"
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


def message(message_id, *, text=None, location=None, action_id=None, channel=MessagingChannel.WHATSAPP):
    return NormalizedInboundMessage(
        channel=channel,
        external_message_id=message_id,
        external_user_id="seller",
        message_type=MessageType.INTERACTIVE if action_id else (MessageType.LOCATION if location else MessageType.TEXT),
        text=text,
        location=location,
        metadata={"action_id": action_id} if action_id else {},
    )


@pytest.mark.django_db
def test_sell_onboarding_creates_store_and_owner_only_after_confirm():
    convo = conversation()
    app = ConversationalOrchestrator(understanding_service=NoLLM())

    start = app.process_message(convo, message("1", action_id="journey.select.sell"))
    assert start.response.metadata["seller_onboarding"] is True
    assert Store.objects.count() == 0
    convo.refresh_from_db()
    app.process_message(convo, message("2", text="Ma boutique s'appelle Chez Jacob"))
    convo.refresh_from_db()
    app.process_message(convo, message("3", location=MessageLocation(latitude=4.05, longitude=9.70)))
    convo.refresh_from_db()
    review = app.process_message(convo, message("4", text="Douala"))
    assert review.response.actions[0].action_id == "seller_onboarding.confirm"
    assert Store.objects.count() == 0
    convo.refresh_from_db()
    completed = app.process_message(convo, message("5", action_id="seller_onboarding.confirm"))

    store = Store.objects.get()
    assert store.name == "Chez Jacob"
    assert store.city == "Douala"
    assert store.location.srid == 4326
    assert (store.location.x, store.location.y) == (9.70, 4.05)
    assert StoreMembership.objects.get(store=store, user=convo.user).role == StoreMembershipRole.OWNER
    assert convo.user.roles.filter(role=UserRoleType.STORE_OWNER).exists()
    assert [action.action_id for action in completed.response.actions] == ["seller.catalog.chat", "seller.catalog.dashboard"]


@pytest.mark.django_db
@pytest.mark.parametrize("channel", [MessagingChannel.WHATSAPP, MessagingChannel.TELEGRAM])
def test_native_location_is_channel_agnostic(channel):
    convo = conversation(channel)
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    app.process_message(convo, message("1", action_id="journey.select.sell", channel=channel))
    convo.refresh_from_db()
    app.process_message(convo, message("2", text="Chez Canal", channel=channel))
    convo.refresh_from_db()
    app.process_message(
        convo,
        message("3", location=MessageLocation(latitude=3.9, longitude=11.5), channel=channel),
    )
    convo.refresh_from_db()
    draft = ConversationContext.from_payload(convo.context).seller_onboarding
    assert (draft.latitude, draft.longitude) == (3.9, 11.5)
    assert draft.stage == SellerOnboardingStage.CITY


@pytest.mark.django_db
def test_edit_cancel_resume_and_double_confirm_are_persistent_and_idempotent():
    convo = conversation()
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    app.process_message(convo, message("1", action_id="journey.select.sell"))
    convo.refresh_from_db()
    app.process_message(convo, message("2", text="Nom initial"))
    convo.refresh_from_db()
    app.process_message(convo, message("3", action_id="seller_onboarding.cancel"))
    assert Store.objects.count() == 0
    convo.refresh_from_db()
    app.process_message(convo, message("4", action_id="journey.select.sell"))
    convo.refresh_from_db()
    app.process_message(convo, message("5", text="Nom final"))
    convo.refresh_from_db()
    app.process_message(convo, message("6", location=MessageLocation(latitude=4, longitude=9)))
    convo.refresh_from_db()
    app.process_message(convo, message("7", text="Douala"))
    convo.refresh_from_db()
    app.process_message(convo, message("8", action_id="seller_onboarding.confirm"))
    convo.refresh_from_db()
    app.process_message(convo, message("9", action_id="seller_onboarding.confirm"))
    assert Store.objects.count() == 1
    assert StoreMembership.objects.count() == 1


@pytest.mark.django_db
def test_completed_onboarding_rejects_old_review_actions_and_catalog_alternative():
    convo = conversation()
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    app.process_message(convo, message("1", action_id="journey.select.sell"))
    convo.refresh_from_db()
    app.process_message(convo, message("2", text="Boutique"))
    convo.refresh_from_db()
    app.process_message(convo, message("3", location=MessageLocation(latitude=4, longitude=9)))
    convo.refresh_from_db()
    app.process_message(convo, message("4", text="Douala"))
    convo.refresh_from_db()
    app.process_message(convo, message("5", action_id="seller_onboarding.confirm"))
    assert Store.objects.count() == 1

    for message_id, action_id in (("6", "seller_onboarding.edit"), ("7", "seller_onboarding.cancel")):
        stale = app.process_message(convo, message(message_id, action_id=action_id))
        assert stale.metadata["stale_action"] is True
    convo.refresh_from_db()
    assert ConversationContext.from_payload(convo.context).seller_onboarding.stage == SellerOnboardingStage.COMPLETED

    committed = app.process_message(convo, message("8", action_id="seller.catalog.chat"))
    assert committed.metadata["catalog_setup_choice"] == "chat"
    stale = app.process_message(convo, message("9", action_id="seller.catalog.dashboard"))
    assert stale.metadata["stale_action"] is True
    convo.refresh_from_db()
    assert ConversationContext.from_payload(convo.context).catalog_setup_choice == "chat"
    assert Store.objects.count() == 1


@pytest.mark.django_db
def test_cancel_returns_journey_selector_and_old_confirm_is_non_mutating():
    convo = conversation(MessagingChannel.TELEGRAM)
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    app.process_message(convo, message("1", action_id="journey.select.sell", channel=MessagingChannel.TELEGRAM))
    convo.refresh_from_db()
    app.process_message(convo, message("2", text="Boutique", channel=MessagingChannel.TELEGRAM))
    convo.refresh_from_db()
    app.process_message(convo, message("3", location=MessageLocation(latitude=4, longitude=9), channel=MessagingChannel.TELEGRAM))
    convo.refresh_from_db()
    app.process_message(convo, message("4", text="Douala", channel=MessagingChannel.TELEGRAM))
    convo.refresh_from_db()
    cancelled = app.process_message(convo, message("5", action_id="seller_onboarding.cancel", channel=MessagingChannel.TELEGRAM))
    assert [action.action_id for action in cancelled.response.actions] == ["journey.select.buy", "journey.select.sell", "journey.select.deliver"]
    app.process_message(convo, message("6", action_id="seller_onboarding.confirm", channel=MessagingChannel.TELEGRAM))
    assert Store.objects.count() == 0


@pytest.mark.django_db
def test_existing_store_does_not_start_or_create_another_store():
    convo = conversation()
    Store.objects.create(
        name="Existante", city="Douala", location=Point(9.7, 4.05, srid=4326)
    )
    store = Store.objects.get()
    StoreMembership.objects.create(store=store, user=convo.user, role=StoreMembershipRole.OWNER)
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    outcome = app.process_message(convo, message("1", action_id="journey.select.sell"))
    assert outcome.metadata["journey_status"] == "SELLER_JOURNEY_READY"
    assert Store.objects.count() == 1


def test_invalid_coordinates_are_rejected_without_geocoding():
    assert not SellerOnboardingService._valid_coordinates(91, 9)
    assert not SellerOnboardingService._valid_coordinates(4, 181)
    assert not SellerOnboardingService._valid_coordinates(float("inf"), 9)


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmation_creates_one_store_and_membership():
    from django.db import close_old_connections

    from apps.conversations.infrastructure.models import Conversation

    convo = conversation()
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    app.process_message(convo, message("1", action_id="journey.select.sell"))
    convo.refresh_from_db()
    app.process_message(convo, message("2", text="Chez Jacob"))
    convo.refresh_from_db()
    app.process_message(convo, message("3", location=MessageLocation(latitude=4, longitude=9)))
    convo.refresh_from_db()
    app.process_message(convo, message("4", text="Douala"))

    barrier = threading.Barrier(2)
    errors = []

    def confirm_in_parallel():
        try:
            close_old_connections()
            parallel_conversation = Conversation.objects.get(pk=convo.pk)
            barrier.wait()
            SellerOnboardingService().confirm(parallel_conversation)
        except Exception as exc:  # surfaced below, never hidden by the worker
            errors.append(exc)
        finally:
            close_old_connections()

    workers = [threading.Thread(target=confirm_in_parallel) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not errors
    assert not any(worker.is_alive() for worker in workers)
    assert Store.objects.count() == 1
    assert StoreMembership.objects.count() == 1
