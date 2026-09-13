import uuid


class CorrelationIdMiddleware:
    HEADER_NAME = "HTTP_X_CORRELATION_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        correlation_id = request.META.get(self.HEADER_NAME)

        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        request.correlation_id = correlation_id

        response = self.get_response(request)

        response["X-Correlation-ID"] = correlation_id

        return response
