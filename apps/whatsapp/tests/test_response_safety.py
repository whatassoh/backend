from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from django.test import override_settings

from apps.conversations.application.orchestrator import (
    ConversationalOrchestrator,
    ResponseAction,
    ResponsePlan,
)
from apps.conversations.application.order_review_presentation import (
    OrderReviewPresenter,
)
from apps.conversations.application.responding import send_response_plan
from apps.conversations.domain.context import ConversationContext
from apps.messaging.domain.enums import MessagingChannel
from apps.orders.application.review import (
    OrderConfirmationReadiness,
    OrderReview,
    OrderReviewItem,
)
from apps.whatsapp.adapters.outbound import (
    build_interactive_list_payload,
    build_interactive_reply_buttons_payload,
)
from apps.whatsapp.domain.dtos import (
    WhatsAppInteractiveListMessage,
    WhatsAppInteractiveMessage,
    WhatsAppReplyButton,
)
from apps.whatsapp.domain.response_validation import (
    WhatsAppInteractiveMode,
    validate_response_for_channel,
)
from apps.whatsapp.exceptions import WhatsAppAPIError, WhatsAppInvalidPayloadError
from apps.whatsapp.providers.cloud_api import WhatsAppProvider


def ready_review():
    return OrderReview(
        order_id="order-1",
        reference="CMD-1",
        items=(
            OrderReviewItem(
                order_item_id="item-1",
                requested_name="Riz",
                product_name="Riz",
                variant_name=None,
                quantity="3",
                unit="KG",
                resolution_status="MATCHED",
                display_label="3 kg × Riz",
            ),
        ),
        delivery_address_label=None,
        has_delivery_location=True,
        delivery_instructions=None,
        readiness=OrderConfirmationReadiness(ready=True),
        available_actions=("order.confirm", "order.modify", "order.cancel"),
        fingerprint="review-1",
    )


def test_order_recap_has_maximum_three_whatsapp_buttons():
    presentation = OrderReviewPresenter().present(ready_review())

    assert presentation.actions == (
        ("order.confirm", "Confirmer"),
        ("order.modify", "Modifier"),
        ("order.cancel", "Annuler"),
    )


def test_order_recap_is_whatsapp_deliverable():
    presentation = OrderReviewPresenter().present(ready_review())
    response = ResponsePlan(
        text=presentation.text,
        actions=tuple(
            ResponseAction(action_id=action_id, label=label)
            for action_id, label in presentation.actions
        ),
    )

    assert (
        validate_response_for_channel(response, MessagingChannel.WHATSAPP)
        == WhatsAppInteractiveMode.REPLY_BUTTONS
    )
    payload = build_interactive_reply_buttons_payload(
        WhatsAppInteractiveMessage(
            recipient_external_id="237699887766",
            body=response.text,
            buttons=[
                WhatsAppReplyButton(action_id=action.action_id, label=action.label)
                for action in response.actions
            ],
        )
    )
    assert len(payload["interactive"]["action"]["buttons"]) == 3


def test_order_modify_menu_exposes_address_change():
    outcome = ConversationalOrchestrator(
        understanding_service=SimpleNamespace()
    )._order_modify_menu(ConversationContext())

    assert [action.action_id for action in outcome.response.actions] == [
        "order.modify.products",
        "order.change_location",
        "order.modify.back",
    ]


def test_delivery_issue_menu_is_whatsapp_safe():
    orchestrator = ConversationalOrchestrator(understanding_service=SimpleNamespace())
    primary = orchestrator._delivery_action(
        SimpleNamespace(), ConversationContext(), "delivery.report_issue", None
    )
    secondary = orchestrator._delivery_action(
        SimpleNamespace(), ConversationContext(), "delivery.issue.more", None
    )

    assert len(primary.response.actions) == 3
    assert len(secondary.response.actions) == 3
    assert "delivery.issue.vehicle_problem" in {
        action.action_id for action in secondary.response.actions
    }


def test_all_static_whatsapp_action_menus_are_valid():
    menus = (
        OrderReviewPresenter().present(ready_review()).actions,
        (
            ("delivery.issue.customer_unreachable", "Client injoignable"),
            ("delivery.issue.address_problem", "Problème d'adresse"),
            ("delivery.issue.more", "Autre problème"),
        ),
        (
            ("delivery.issue.vehicle_problem", "Problème de véhicule"),
            ("delivery.issue.other", "Autre problème"),
            ("delivery.report_issue", "Retour"),
        ),
    )
    for actions in menus:
        response = ResponsePlan(
            text="Menu",
            actions=tuple(
                ResponseAction(action_id=action_id, label=label)
                for action_id, label in actions
            ),
        )
        assert (
            validate_response_for_channel(response, MessagingChannel.WHATSAPP)
            == WhatsAppInteractiveMode.REPLY_BUTTONS
        )


def test_whatsapp_rejects_more_than_three_reply_buttons():
    buttons = [
        WhatsAppReplyButton(action_id=f"action.{index}", label=f"Action {index}")
        for index in range(4)
    ]

    with pytest.raises(ValueError, match="between 1 and 3"):
        build_interactive_reply_buttons_payload(
            WhatsAppInteractiveMessage(
                recipient_external_id="237699887766", body="Menu", buttons=buttons
            )
        )


@pytest.mark.parametrize(
    "action",
    (
        ResponseAction(action_id="", label="Action"),
        ResponseAction(action_id="action.valid", label=" "),
    ),
)
def test_whatsapp_rejects_empty_response_action_fields(action):
    with pytest.raises(WhatsAppInvalidPayloadError):
        validate_response_for_channel(
            ResponsePlan(text="Menu", actions=(action,)), MessagingChannel.WHATSAPP
        )


def test_whatsapp_uses_list_without_losing_actions_above_three():
    response = ResponsePlan(
        text="Choisissez une action",
        actions=tuple(
            ResponseAction(action_id=f"action.{index}", label=f"Action {index}")
            for index in range(4)
        ),
    )

    assert (
        validate_response_for_channel(response, MessagingChannel.WHATSAPP)
        == WhatsAppInteractiveMode.LIST
    )
    payload = build_interactive_list_payload(
        WhatsAppInteractiveListMessage(
            recipient_external_id="237699887766",
            body=response.text,
            rows=[
                WhatsAppReplyButton(action_id=action.action_id, label=action.label)
                for action in response.actions
            ],
        )
    )
    rows = payload["interactive"]["action"]["sections"][0]["rows"]
    row_ids = [row["id"] for row in rows]
    action_ids = [action.action_id for action in response.actions]
    assert row_ids == action_ids


def test_whatsapp_rejects_menu_larger_than_interactive_list_limit():
    response = ResponsePlan(
        text="Choisissez une action",
        actions=tuple(
            ResponseAction(action_id=f"action.{index}", label=f"Action {index}")
            for index in range(11)
        ),
    )

    with pytest.raises(WhatsAppInvalidPayloadError, match="at most 10"):
        validate_response_for_channel(response, MessagingChannel.WHATSAPP)


def test_response_plan_with_four_actions_is_sent_as_a_list(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        "apps.whatsapp.application.outbound.send_whatsapp_interactive_list",
        lambda **kwargs: sent.update(kwargs),
    )
    response = ResponsePlan(
        text="Choisissez une action",
        actions=tuple(
            ResponseAction(action_id=f"action.{index}", label=f"Action {index}")
            for index in range(4)
        ),
    )
    conversation = SimpleNamespace(
        channel=MessagingChannel.WHATSAPP,
        external_chat_id="237699887766",
        channel_identity=SimpleNamespace(external_user_id="237699887766"),
    )

    send_response_plan(conversation=conversation, response=response)

    assert [row.action_id for row in sent["rows"]] == [
        action.action_id for action in response.actions
    ]


@override_settings(
    WHATSAPP_ENABLED=True,
    WHATSAPP_ACCESS_TOKEN="token",
    WHATSAPP_PHONE_NUMBER_ID="phone-number-id",
    WHATSAPP_VERIFY_TOKEN="verify",
    WHATSAPP_APP_SECRET="secret",
    META_GRAPH_API_VERSION="v26.0",
)
def test_whatsapp_api_failure_logs_meta_diagnostics(monkeypatch, caplog):
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            400,
            "Bad request",
            {},
            BytesIO(
                b'{"error":{"message":"invalid title","type":"OAuthException",'
                b'"code":100,"error_subcode":2494073}}'
            ),
        )

    monkeypatch.setattr("apps.whatsapp.providers.cloud_api.urlopen", fake_urlopen)
    with pytest.raises(WhatsAppAPIError):
        WhatsAppProvider()._post_message(
            {"type": "interactive", "interactive": {"type": "button"}}
        )

    record = next(
        record
        for record in caplog.records
        if record.msg == "whatsapp_api_request_failed"
    )
    assert record.http_status == 400
    assert record.meta_error_code == 100
    assert record.meta_error_subcode == 2494073
    assert record.meta_error_type == "OAuthException"
    assert record.meta_error_message == "invalid title"
