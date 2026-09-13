from typing import Any

from rest_framework.response import Response
from rest_framework.views import exception_handler


def api_exception_handler(
    exc: Exception,
    context: dict[str, Any],
) -> Response | None:
    response = exception_handler(exc, context)

    if response is None:
        return None

    error_code = "API_ERROR"

    if response.status_code == 400:
        error_code = "VALIDATION_ERROR"

    elif response.status_code == 401:
        error_code = "AUTHENTICATION_REQUIRED"

    elif response.status_code == 403:
        error_code = "PERMISSION_DENIED"

    elif response.status_code == 404:
        error_code = "NOT_FOUND"

    elif response.status_code == 405:
        error_code = "METHOD_NOT_ALLOWED"

    elif response.status_code == 429:
        error_code = "RATE_LIMITED"

    original_data = response.data

    response.data = {
        "error": {
            "code": error_code,
            "message": _extract_message(original_data),
            "details": original_data,
        }
    }

    return response


def _extract_message(data: Any) -> str:
    if isinstance(data, dict):
        detail = data.get("detail")

        if detail:
            return str(detail)

        return "The request contains invalid data."

    if isinstance(data, list) and data:
        return str(data[0])

    return "An API error occurred."
