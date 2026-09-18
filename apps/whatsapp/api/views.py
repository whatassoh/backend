import json

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
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
    """Webhook Meta entrant; protégé par verify token ou signature HMAC."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = []

    @extend_schema(
        tags=["Webhooks / WhatsApp"],
        summary="Verifier le webhook Meta",
        parameters=[
            OpenApiParameter(
                "hub.mode", OpenApiTypes.STR, OpenApiParameter.QUERY, required=True
            ),
            OpenApiParameter(
                "hub.verify_token",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                required=True,
            ),
            OpenApiParameter(
                "hub.challenge", OpenApiTypes.STR, OpenApiParameter.QUERY, required=True
            ),
        ],
        responses={200: OpenApiTypes.STR, 400: None, 403: None, 503: None},
    )
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

    @extend_schema(
        tags=["Webhooks / WhatsApp"],
        summary="Recevoir un evenement WhatsApp",
        description=(
            "Payload Meta brut. La signature `X-Hub-Signature-256` est obligatoire; "
            "le traitement est idempotent par identifiant externe."
        ),
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 400: None, 403: None, 503: None},
    )
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
