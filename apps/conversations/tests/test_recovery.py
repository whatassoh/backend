import pytest
from django.contrib.gis.geos import Point

from apps.conversations.application.active_orders import ActiveCustomerOrderService
from apps.conversations.application.recovery import (
    ConversationRecoveryService,
    RecoveryStatus,
)
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import (
    ClarificationKind,
    ConversationContext,
    PendingClarification,
)
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.enums import MessagingChannel
from apps.orders.application.services import add_order_item, set_delivery_location


def conversation():
    user = User.objects.create_user(whatsapp_phone="+237699887766")
    identity = create_channel_identity(
        user=user, channel=MessagingChannel.WHATSAPP, external_user_id="237699887766"
    )
    return get_or_create_active_conversation(channel_identity=identity)[0]


@pytest.mark.django_db
def test_recovery_requests_location_from_persisted_draft():
    convo = conversation()
    active = ActiveCustomerOrderService().get_or_create_draft(convo)
    add_order_item(order=active.order, requested_name="Riz", quantity=1, unit="KG")
    convo.refresh_from_db()

    decision = ConversationRecoveryService().recover(
        convo, ConversationContext.from_payload(convo.context)
    )

    assert decision.status == RecoveryStatus.REQUEST_LOCATION
    assert decision.order_id == str(active.order.id)


@pytest.mark.django_db
def test_recovery_resumes_clarification_before_review():
    convo = conversation()
    active = ActiveCustomerOrderService().get_or_create_draft(convo)
    convo.context = ConversationContext(
        active_order_id=str(active.order.id),
        pending_clarification=PendingClarification(
            kind=ClarificationKind.MISSING_QUANTITY, field="quantity", item_reference="riz"
        ),
    ).to_payload()
    convo.save(update_fields=["context", "updated_at"])

    decision = ConversationRecoveryService().recover(
        convo, ConversationContext.from_payload(convo.context)
    )

    assert decision.status == RecoveryStatus.RESUME_CLARIFICATION


@pytest.mark.django_db
def test_recovery_rebuilds_ready_review_from_order_rows():
    convo = conversation()
    active = ActiveCustomerOrderService().get_or_create_draft(convo)
    add_order_item(order=active.order, requested_name="Riz", quantity=1, unit="KG")
    set_delivery_location(active.order, Point(9.7, 4.05, srid=4326))
    convo.refresh_from_db()

    decision = ConversationRecoveryService().recover(
        convo, ConversationContext.from_payload(convo.context)
    )

    assert decision.status == RecoveryStatus.REVIEW_READY
