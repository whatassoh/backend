import json

from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.whatsapp.application.services import process_whatsapp_webhook
from apps.whatsapp.conf import get_whatsapp_settings
from apps.whatsapp.exceptions import (
    WhatsAppConfigurationError,
    WhatsAppInvalidPayloadError,
)
from apps.whatsapp.security import verify_meta_signature


class WhatsAppWebhookView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = []

    def get(self, request):
        try:
            config = get_whatsapp_settings(require_enabled=True)
        except WhatsAppConfigurationError:
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if not mode or not token or not challenge:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        if mode == "subscribe" and token == config.verify_token:
            return HttpResponse(challenge, content_type="text/plain")
        return Response(status=status.HTTP_403_FORBIDDEN)

    def post(self, request):
        try:
            config = get_whatsapp_settings(require_enabled=True)
        except WhatsAppConfigurationError:
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not verify_meta_signature(
            raw_body=request.body,
            signature_header=request.headers.get("X-Hub-Signature-256"),
            app_secret=config.app_secret,
        ):
            return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            payload = json.loads(request.body.decode("utf-8"))
            result = process_whatsapp_webhook(payload)
        except json.JSONDecodeError:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except WhatsAppInvalidPayloadError:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)
