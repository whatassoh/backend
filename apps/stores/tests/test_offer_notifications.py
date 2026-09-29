from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from apps.messaging.domain.exceptions import MessagingRateLimitError, MessagingTemporaryError
from apps.stores.application.offer_notifications import (
    OutboundInstruction, ProviderErrorCategory, StoreOfferNotificationRenderer, classify_provider_error,
    retry_delay_seconds,
)
from apps.stores.tasks import _dispatch
from apps.whatsapp.conf import get_whatsapp_settings
from apps.whatsapp.domain.enums import WhatsAppTemplateKey
from apps.whatsapp.domain.templates import validate_template_parameters


class StoreOfferNotificationBoundaryTests(SimpleTestCase):
    def test_retryable_errors_are_not_business_rejections(self):
        result = classify_provider_error(MessagingRateLimitError("slow", retry_after=12))
        self.assertEqual(result.category, ProviderErrorCategory.RATE_LIMIT)
        self.assertTrue(result.retryable)
        self.assertEqual(result.retry_after, 12)
        self.assertTrue(classify_provider_error(MessagingTemporaryError("timeout")).retryable)

    def test_telegram_renderer_keeps_stable_business_actions(self):
        notification = SimpleNamespace(
            channel="TELEGRAM",
            presentation_snapshot={
                "order_reference": "ORD-1", "items": ["2 UNIT Mayor"],
                "expires_at": "2026-01-01T00:00:00Z",
                "actions": [{"id": "store_offer.accept", "offer_id": "offer"}, {"id": "store_offer.reject", "offer_id": "offer"}],
            },
        )
        instruction = StoreOfferNotificationRenderer().render(notification)
        self.assertEqual(instruction.kind, "interactive")
        self.assertEqual(tuple(item["id"] for item in instruction.actions), ("store_offer.accept", "store_offer.reject"))

    def test_retry_delay_is_bounded(self):
        self.assertEqual(retry_delay_seconds(1), 30)
        self.assertLessEqual(retry_delay_seconds(100), 900)

    @override_settings(WHATSAPP_ENABLED=False)
    def test_operational_template_uses_named_recipient_contract(self):
        definition = get_whatsapp_settings().templates[
            WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED
        ]
        validate_template_parameters(
            definition, body_parameters={"recipient_name": "Amina"}
        )


def test_closed_whatsapp_dispatch_passes_valid_named_parameter(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "apps.whatsapp.application.outbound.send_whatsapp_template_to_conversation",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )
    notification = SimpleNamespace(
        channel="WHATSAPP",
        offer_id="offer",
        conversation=SimpleNamespace(),
        recipient=SimpleNamespace(first_name="Amina"),
        presentation_snapshot={"order_reference": "ORD-1"},
    )
    instruction = OutboundInstruction(
        channel="WHATSAPP",
        kind="template",
        text="Action requise",
        actions=(),
        template_key=WhatsAppTemplateKey.OPERATIONAL_ACTION_REQUIRED,
    )
    _dispatch(notification, instruction, "correlation")
    assert captured["body_parameters"] == {"recipient_name": "Amina"}
