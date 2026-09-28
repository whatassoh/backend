from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point

from apps.ai.domain.understanding import MessageIntent, UserMessageUnderstanding
from apps.catalog.application.services import (
    add_product_to_store,
    create_category,
    create_product,
)
from apps.catalog.domain.enums import ProductAvailability
from apps.conversations.application.interruptions import (
    UniversalCommand,
    detect_universal_command,
    suspend_active_task,
)
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import (
    ActiveJourney,
    ConversationContext,
    ConversationFlow,
    ConversationSocialState,
    ConversationTaskKind,
    DriverOnboardingDraft,
    DriverOnboardingStage,
    PendingSellerCatalogMutation,
    SellerCatalogAction,
    SellerOnboardingDraft,
    SellerOnboardingStage,
    SuspendedConversationTask,
)
from apps.drivers.infrastructure.models import DriverProfile
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.stores.application.services import create_store
from apps.stores.infrastructure.models import Store


class UnderstandingQueue:
    def __init__(self, *understandings):
        self.understandings = list(understandings)
        self.called = 0
        self.last_kwargs = {}

    def understand_normalized(self, *_args, **kwargs):
        self.called += 1
        self.last_kwargs = kwargs
        return self.understandings.pop(0) if self.understandings else None


def conversation():
    user = User.objects.create_user(
        whatsapp_phone=f"+2376{User.objects.count():08d}", first_name="Louis"
    )
    identity = create_channel_identity(
        user=user,
        channel=MessagingChannel.WHATSAPP,
        external_user_id=f"interrupt-{user.id}",
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


def message(text=None, *, action_id=None, message_id="message-1"):
    return NormalizedInboundMessage(
        channel=MessagingChannel.WHATSAPP,
        external_message_id=message_id,
        external_user_id="interrupt-user",
        message_type=MessageType.INTERACTIVE if action_id else MessageType.TEXT,
        text=text,
        metadata={"action_id": action_id} if action_id else {},
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Annuler !", UniversalCommand.CANCEL),
        ("on reprend plus tard", UniversalCommand.PAUSE),
        ("reprendre", UniversalCommand.RESUME),
        ("revenir en arrière", UniversalCommand.BACK),
        ("AIDE", UniversalCommand.HELP),
        ("Bonjour", UniversalCommand.GREETING),
        ("Qu’as-tu mémorisé sur moi ?", UniversalCommand.MEMORY_SHOW),
        ("Oublie mes préférences", UniversalCommand.MEMORY_FORGET),
    ],
)
def test_exact_universal_commands_are_accent_and_case_tolerant(text, expected):
    assert detect_universal_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "annule la commande de Paul",
        "bonjour je veux trois kilos de riz",
        "continue avec une bouteille d'huile",
    ],
)
def test_rich_messages_are_not_reduced_to_a_keyword(text):
    assert detect_universal_command(text) is None


def test_extended_context_round_trip_is_backward_compatible():
    old_context = ConversationContext.from_payload(
        {"flow": "ORDER_BUILDING", "active_order_id": "order-id"}
    )
    assert old_context.suspended_tasks == ()
    assert old_context.social_state == ConversationSocialState()

    context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        suspended_tasks=(
            SuspendedConversationTask(
                kind=ConversationTaskKind.ORDER,
                journey=ActiveJourney.BUY,
                flow=ConversationFlow.ORDER_BUILDING,
                suspended_at="2026-09-24T12:00:00+00:00",
                reason="topic_change",
            ),
        ),
        social_state=ConversationSocialState(
            greeting_count=2,
            last_greeting_at="2026-09-24T12:00:00+00:00",
            introduced_at="2026-09-24T10:00:00+00:00",
        ),
    )
    assert ConversationContext.from_payload(context.to_payload()) == context


@pytest.mark.django_db
def test_natural_pause_sentence_is_understood_with_active_task_context():
    convo = conversation()
    convo.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION, store_name="Chez Louis"
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    understanding = UnderstandingQueue(
        UserMessageUnderstanding(intent=MessageIntent.PAUSE, confidence=Decimal(".98"))
    )

    outcome = ConversationalOrchestrator(
        understanding_service=understanding
    ).process_message(
        convo,
        message("Je préfère continuer la création de ma boutique demain"),
    )

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert understanding.called == 1
    assert understanding.last_kwargs["limited_context"] == {
        "active_task": "SELLER_ONBOARDING",
        "active_journey": "SELL",
        "flow": "SELLER_ONBOARDING",
        "has_suspended_task": False,
    }
    assert "pause" in outcome.response.text
    assert context.suspended_tasks[-1].kind == ConversationTaskKind.SELLER_ONBOARDING
    assert context.seller_onboarding.store_name == "Chez Louis"


@pytest.mark.django_db
def test_natural_cancel_sentence_cancels_the_contextual_task():
    convo = conversation()
    convo.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION, store_name="Chez Louis"
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    understanding = UnderstandingQueue(
        UserMessageUnderstanding(intent=MessageIntent.CANCEL, confidence=Decimal(".98"))
    )

    outcome = ConversationalOrchestrator(
        understanding_service=understanding
    ).process_message(
        convo,
        message("Finalement, je ne veux plus créer cette boutique"),
    )

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert "Aucune boutique n'a été créée" in outcome.response.text
    assert context.seller_onboarding is None
    assert not Store.objects.exists()


@pytest.mark.django_db
def test_catalog_question_reads_live_products_without_consuming_seller_onboarding():
    convo = conversation()
    original = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION,
            store_name="Chez Louis",
        ),
    )
    convo.context = original.to_payload()
    convo.save(update_fields=["context", "updated_at"])
    store = create_store(
        name="Boutique active",
        location=Point(9.7, 4.05, srid=4326),
        city="Douala",
    )
    product = create_product(
        name="Riz parfumé",
        category=create_category(name="Alimentation"),
    )
    add_product_to_store(
        store=store,
        product=product,
        availability_status=ProductAvailability.AVAILABLE,
    )
    understanding = UnderstandingQueue(
        UserMessageUnderstanding(
            intent=MessageIntent.CATALOG_BROWSE,
            confidence=Decimal(".99"),
        )
    )

    outcome = ConversationalOrchestrator(
        understanding_service=understanding
    ).process_message(
        convo,
        message("Est-ce qu'il y a déjà des produits disponibles ?"),
    )

    convo.refresh_from_db()
    assert "Riz parfumé" in outcome.response.text
    assert outcome.metadata["authoritative"] is True
    assert ConversationContext.from_payload(convo.context).seller_onboarding == (
        original.seller_onboarding
    )


@pytest.mark.django_db
def test_ambiguous_natural_cancel_never_mutates_the_active_task():
    convo = conversation()
    original = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION, store_name="Chez Louis"
        ),
    )
    convo.context = original.to_payload()
    convo.save(update_fields=["context", "updated_at"])
    understanding = UnderstandingQueue(
        UserMessageUnderstanding(
            intent=MessageIntent.CANCEL,
            confidence=Decimal(".98"),
            ambiguities=("target_to_cancel",),
        )
    )

    outcome = ConversationalOrchestrator(
        understanding_service=understanding
    ).process_message(convo, message("Annule ça, mais garde la boutique"))

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert "Que souhaitez-vous annuler" in outcome.response.text
    assert context.seller_onboarding == original.seller_onboarding
    assert context.active_journey == ActiveJourney.SELL


@pytest.mark.django_db
def test_natural_back_sentence_uses_the_active_workflow_stage():
    convo = conversation()
    convo.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.CITY,
            store_name="Chez Louis",
            latitude=4.05,
            longitude=9.7,
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    understanding = UnderstandingQueue(
        UserMessageUnderstanding(intent=MessageIntent.BACK, confidence=Decimal(".98"))
    )

    ConversationalOrchestrator(understanding_service=understanding).process_message(
        convo,
        message("Je voudrais revenir à l'étape précédente, s'il te plaît"),
    )

    convo.refresh_from_db()
    draft = ConversationContext.from_payload(convo.context).seller_onboarding
    assert draft.stage == SellerOnboardingStage.LOCATION
    assert draft.store_name == "Chez Louis"


@pytest.mark.django_db
def test_natural_resume_sentence_restores_the_suspended_task():
    convo = conversation()
    suspended, _ = suspend_active_task(
        ConversationContext(
            active_journey=ActiveJourney.SELL,
            flow=ConversationFlow.SELLER_ONBOARDING,
            seller_onboarding=SellerOnboardingDraft(
                stage=SellerOnboardingStage.LOCATION,
                store_name="Chez Louis",
            ),
        ),
        reason="user_pause",
    )
    convo.context = suspended.to_payload()
    convo.save(update_fields=["context", "updated_at"])
    understanding = UnderstandingQueue(
        UserMessageUnderstanding(
            intent=MessageIntent.ORDER_RESUME, confidence=Decimal(".98")
        )
    )

    outcome = ConversationalOrchestrator(
        understanding_service=understanding
    ).process_message(
        convo,
        message("Maintenant, on peut reprendre la boutique là où nous étions"),
    )

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert context.active_journey == ActiveJourney.SELL
    assert context.seller_onboarding.stage == SellerOnboardingStage.LOCATION
    assert outcome.response.request_location is True


@pytest.mark.django_db
def test_greeting_during_seller_location_preserves_stage_without_model_call():
    convo = conversation()
    convo.context = ConversationContext(
        flow=ConversationFlow.SELLER_ONBOARDING,
        active_journey=ActiveJourney.SELL,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION,
            store_name="Chez Louis",
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    understanding = UnderstandingQueue()

    outcome = ConversationalOrchestrator(
        understanding_service=understanding
    ).process_message(convo, message("Bonjour"))

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert understanding.called == 0
    assert outcome.response.text.startswith(("Bonjour Louis", "Bonsoir Louis"))
    assert "position" not in outcome.response.text.casefold()
    assert context.seller_onboarding.stage == SellerOnboardingStage.LOCATION
    assert context.social_state.greeting_count == 1


@pytest.mark.django_db
def test_repeated_greeting_does_not_repeat_introduction():
    convo = conversation()
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())
    convo.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(stage=SellerOnboardingStage.NAME),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])

    first = app.process_message(convo, message("bonjour", message_id="greeting-1"))
    convo.refresh_from_db()
    second = app.process_message(convo, message("salut", message_id="greeting-2"))

    assert "assistant WhatAssoh" in first.response.text
    assert "assistant WhatAssoh" not in second.response.text
    assert "Rebonjour Louis" in second.response.text


@pytest.mark.django_db
def test_cancel_seller_onboarding_at_location_discards_only_uncommitted_draft():
    convo = conversation()
    convo.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION, store_name="Chez Louis"
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])

    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue()
    ).process_message(convo, message("annuler"))

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert "Aucune boutique n'a été créée" in outcome.response.text
    assert context.seller_onboarding is None
    assert context.active_journey is None
    assert not Store.objects.exists()


@pytest.mark.django_db
def test_pause_and_resume_seller_location_preserve_all_collected_fields():
    convo = conversation()
    convo.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION, store_name="Chez Louis"
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())

    paused = app.process_message(convo, message("plus tard", message_id="pause"))
    convo.refresh_from_db()
    paused_context = ConversationContext.from_payload(convo.context)
    resumed = app.process_message(convo, message("reprendre", message_id="resume"))

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert "pause" in paused.response.text
    assert paused_context.active_journey is None
    assert (
        paused_context.suspended_tasks[-1].kind
        == ConversationTaskKind.SELLER_ONBOARDING
    )
    assert context.active_journey == ActiveJourney.SELL
    assert context.seller_onboarding.store_name == "Chez Louis"
    assert context.seller_onboarding.stage == SellerOnboardingStage.LOCATION
    assert resumed.response.request_location is True


@pytest.mark.django_db
def test_structured_topic_switch_suspends_seller_and_resume_restores_it():
    convo = conversation()
    convo.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.LOCATION, store_name="Chez Louis"
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())

    app.process_message(
        convo,
        message(action_id="journey.select.buy", message_id="switch-buy"),
    )
    convo.refresh_from_db()
    switched = ConversationContext.from_payload(convo.context)
    app.process_message(convo, message("reprendre", message_id="resume-seller"))

    convo.refresh_from_db()
    resumed = ConversationContext.from_payload(convo.context)
    assert switched.active_journey == ActiveJourney.BUY
    assert switched.suspended_tasks[-1].kind == ConversationTaskKind.SELLER_ONBOARDING
    assert resumed.active_journey == ActiveJourney.SELL
    assert resumed.seller_onboarding.store_name == "Chez Louis"


@pytest.mark.django_db
def test_back_navigation_is_stage_specific_and_non_destructive():
    convo = conversation()
    convo.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        flow=ConversationFlow.SELLER_ONBOARDING,
        seller_onboarding=SellerOnboardingDraft(
            stage=SellerOnboardingStage.CITY,
            store_name="Chez Louis",
            latitude=4.05,
            longitude=9.7,
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])

    ConversationalOrchestrator(
        understanding_service=UnderstandingQueue()
    ).process_message(convo, message("retour"))

    convo.refresh_from_db()
    draft = ConversationContext.from_payload(convo.context).seller_onboarding
    assert draft.stage == SellerOnboardingStage.LOCATION
    assert draft.store_name == "Chez Louis"
    assert draft.latitude == 4.05
    assert draft.longitude == 9.7


@pytest.mark.django_db
def test_driver_cancel_and_back_never_create_profile():
    convo = conversation()
    convo.context = ConversationContext(
        active_journey=ActiveJourney.DELIVER,
        flow=ConversationFlow.DRIVER_ONBOARDING,
        driver_onboarding=DriverOnboardingDraft(
            stage=DriverOnboardingStage.REVIEW, vehicle_type="MOTORBIKE"
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])
    app = ConversationalOrchestrator(understanding_service=UnderstandingQueue())

    app.process_message(convo, message("retour", message_id="driver-back"))
    convo.refresh_from_db()
    assert (
        ConversationContext.from_payload(convo.context).driver_onboarding.stage
        == DriverOnboardingStage.VEHICLE_TYPE
    )
    app.process_message(convo, message("stop", message_id="driver-cancel"))

    convo.refresh_from_db()
    assert ConversationContext.from_payload(convo.context).driver_onboarding is None
    assert not DriverProfile.objects.exists()


@pytest.mark.django_db
def test_cancel_catalog_proposal_does_not_apply_mutation():
    convo = conversation()
    convo.context = ConversationContext(
        active_journey=ActiveJourney.SELL,
        pending_seller_catalog_mutation=PendingSellerCatalogMutation(
            action=SellerCatalogAction.ADD,
            store_id="00000000-0000-0000-0000-000000000001",
            product_id="00000000-0000-0000-0000-000000000002",
            price="500",
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])

    ConversationalOrchestrator(
        understanding_service=UnderstandingQueue()
    ).process_message(convo, message("annule"))

    convo.refresh_from_db()
    context = ConversationContext.from_payload(convo.context)
    assert context.pending_seller_catalog_mutation is None
    assert context.active_journey is None
