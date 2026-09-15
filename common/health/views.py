from django.http import JsonResponse

from apps.messaging.application.registry import get_channel_configuration_status


def health_check(request):
    return JsonResponse(
        {
            "status": "ok",
            "messaging": get_channel_configuration_status(),
        }
    )
