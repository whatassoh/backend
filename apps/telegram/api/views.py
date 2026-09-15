import json

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conversations.domain.exceptions import UnknownChannelIdentityError
from apps.telegram.application.services import process_telegram_webhook
from apps.telegram.conf import get_telegram_settings
from apps.telegram.exceptions import (
    TelegramConfigurationError,
    TelegramInvalidPayloadError,
)
from apps.telegram.security import verify_telegram_secret


class TelegramWebhookView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = []

    def post(self, request):
        try:
            config = get_telegram_settings(require_enabled=True)
        except TelegramConfigurationError:
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not verify_telegram_secret(
            received_secret=request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
            expected_secret=config.webhook_secret,
        ):
            return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            payload = json.loads(request.body.decode("utf-8"))
            result = process_telegram_webhook(payload)
        except json.JSONDecodeError:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except TelegramInvalidPayloadError:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except UnknownChannelIdentityError as exc:
            return Response(
                {"error": {"code": exc.code, "message": exc.message}},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(result, status=status.HTTP_200_OK)
