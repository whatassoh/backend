from django.urls import path

from apps.telegram.api.views import TelegramWebhookView

urlpatterns = [
    path("webhooks/telegram/", TelegramWebhookView.as_view(), name="telegram-webhook"),
]
