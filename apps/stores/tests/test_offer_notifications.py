from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.messaging.domain.exceptions import MessagingRateLimitError, MessagingTemporaryError
from apps.stores.application.offer_notifications import (
    ProviderErrorCategory, StoreOfferNotificationRenderer, classify_provider_error,
    retry_delay_seconds,
)


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
