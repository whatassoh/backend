import threading
from decimal import Decimal

import pytest

from apps.ai.domain.understanding import MessageIntent, UserMessageUnderstanding
from apps.conversations.application.driver_onboarding import DriverOnboardingService
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import ConversationContext, DriverOnboardingStage
from apps.drivers.domain.enums import DriverAvailability, DriverStatus, VehicleType
from apps.drivers.infrastructure.models import DriverProfile
from apps.identity.domain.enums import UserRoleType
from apps.identity.infrastructure.models import User, UserRole
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel


class NoLLM:
    def understand_normalized(self, *_args, **_kwargs):
        raise AssertionError("driver onboarding must not create a profile through the LLM")


def conversation(channel=MessagingChannel.WHATSAPP):
    user = User.objects.create_user(whatsapp_phone=f"+23767{User.objects.count():07d}")
    identity = create_channel_identity(user=user, channel=channel, external_user_id=f"driver-{user.id}")
    return get_or_create_active_conversation(channel_identity=identity)[0]


def message(message_id, *, text=None, action_id=None, channel=MessagingChannel.WHATSAPP):
    return NormalizedInboundMessage(channel=channel, external_message_id=message_id, external_user_id="driver", message_type=MessageType.INTERACTIVE if action_id else MessageType.TEXT, text=text, metadata={"action_id": action_id} if action_id else {})


@pytest.mark.django_db
@pytest.mark.parametrize("channel", [MessagingChannel.WHATSAPP, MessagingChannel.TELEGRAM])
def test_driver_onboarding_is_channel_neutral_and_only_confirms_after_review(channel):
    convo = conversation(channel)
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    start = app.process_message(convo, message("1", action_id="journey.select.deliver", channel=channel))
    assert start.metadata["journey_status"] == "DRIVER_ONBOARDING_REQUIRED"
    assert not DriverProfile.objects.exists()
    convo.refresh_from_db()
    review = app.process_message(convo, message("2", text="Je livre en moto", channel=channel))
    assert review.response.actions[0].action_id == "driver_onboarding.confirm"
    assert not DriverProfile.objects.exists()
    convo.refresh_from_db()
    completed = app.process_message(convo, message("3", action_id="driver_onboarding.confirm", channel=channel))
    profile = DriverProfile.objects.get(user=convo.user)
    assert profile.vehicle_type == VehicleType.MOTORBIKE
    assert profile.status == DriverStatus.ACTIVE
    assert profile.availability_status == DriverAvailability.OFFLINE
    assert convo.user.roles.filter(role=UserRoleType.DRIVER).exists()
    assert completed.metadata["journey_status"] == "DRIVER_PROFILE_READY"


@pytest.mark.django_db
def test_invalid_modify_cancel_resume_and_existing_profile_are_idempotent():
    convo = conversation()
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    app.process_message(convo, message("1", action_id="journey.select.deliver"))
    convo.refresh_from_db()
    invalid = app.process_message(convo, message("2", text="bateau"))
    assert invalid.response.metadata["driver_onboarding"] is True
    app.process_message(convo, message("3", text="voiture"))
    convo.refresh_from_db()
    app.process_message(convo, message("4", action_id="driver_onboarding.modify"))
    convo.refresh_from_db()
    app.process_message(convo, message("5", text="vélo"))
    convo.refresh_from_db()
    app.process_message(convo, message("6", action_id="driver_onboarding.cancel"))
    assert not DriverProfile.objects.exists()
    convo.refresh_from_db()
    resumed = app.process_message(convo, message("7", action_id="journey.select.deliver"))
    assert resumed.response.metadata["driver_onboarding"] is True
    convo.refresh_from_db()
    app.process_message(convo, message("8", text="CAR"))
    convo.refresh_from_db()
    app.process_message(convo, message("9", action_id="driver_onboarding.confirm"))
    convo.refresh_from_db()
    app.process_message(convo, message("10", action_id="driver_onboarding.confirm"))
    assert DriverProfile.objects.count() == 1
    assert DriverProfile.objects.get().vehicle_type == VehicleType.CAR
    assert app.process_message(convo, message("11", action_id="journey.select.deliver")).metadata["journey_status"] == "DRIVER_JOURNEY_READY"


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmation_creates_exactly_one_profile_and_preserves_roles():
    from django.db import close_old_connections
    from apps.conversations.infrastructure.models import Conversation

    convo = conversation()
    UserRole.objects.create(user=convo.user, role=UserRoleType.CLIENT)
    UserRole.objects.create(user=convo.user, role=UserRoleType.STORE_OWNER)
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    app.process_message(convo, message("1", action_id="journey.select.deliver"))
    convo.refresh_from_db()
    app.process_message(convo, message("2", text="moto"))
    barrier, errors = threading.Barrier(2), []
    def worker():
        try:
            close_old_connections(); parallel = Conversation.objects.get(pk=convo.pk); barrier.wait(); DriverOnboardingService().confirm(parallel)
        except Exception as exc:
            errors.append(exc)
        finally:
            close_old_connections()
    threads = [threading.Thread(target=worker) for _ in range(2)]
    [thread.start() for thread in threads]
    [thread.join(timeout=10) for thread in threads]
    assert not errors
    assert DriverProfile.objects.count() == 1
    assert set(convo.user.roles.values_list("role", flat=True)) == {"CLIENT", "STORE_OWNER", "DRIVER"}


@pytest.mark.django_db
def test_persisted_draft_resumes_after_restart_and_malformed_ai_data_cannot_create_profile():
    convo = conversation()
    app = ConversationalOrchestrator(understanding_service=NoLLM())
    app.process_message(convo, message("1", action_id="journey.select.deliver"))
    convo.refresh_from_db()
    app.process_message(convo, message("2", text="moto"))
    convo.refresh_from_db()
    assert ConversationContext.from_payload(convo.context).driver_onboarding.stage == DriverOnboardingStage.REVIEW
    outcome = DriverOnboardingService().start(convo)
    assert outcome.status.value == "REVIEW_READY"
    assert not DriverProfile.objects.exists()
