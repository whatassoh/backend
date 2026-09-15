from django.urls import path

from apps.whatsapp.api.views import WhatsAppWebhookView

urlpatterns = [
    path("webhooks/whatsapp/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
]
