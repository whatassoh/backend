from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.application.selectors import get_order_audit_history
from apps.audit.domain.enums import AuditActorType
from apps.orders.api.serializers import (
    AuditEventSerializer,
    CancelOrderInputSerializer,
    DeliveryInstructionsInputSerializer,
    DeliveryLocationInputSerializer,
    OrderItemInputSerializer,
    OrderItemSerializer,
    OrderSerializer,
)
from apps.orders.application.selectors import get_order_items
from apps.orders.application.services import (
    add_order_item,
    cancel_order,
    confirm_order,
    create_order_draft,
    remove_order_item,
    retry_failed_order,
    set_delivery_instructions,
    set_delivery_location,
    update_order_item,
)
from apps.orders.domain.exceptions import (
    InvalidCancellation,
    InvalidFailureCode,
    InvalidOrderState,
    InvalidOrderTransition,
    OrderNotConfirmable,
    OrderNotEditable,
    OrderNotRetryable,
)
from apps.orders.infrastructure.models import Order, OrderItem


class OrderDomainAPIException(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "ORDER_DOMAIN_ERROR"


DOMAIN_EXCEPTION_STATUS = {
    OrderNotConfirmable: status.HTTP_400_BAD_REQUEST,
    OrderNotEditable: status.HTTP_409_CONFLICT,
    OrderNotRetryable: status.HTTP_409_CONFLICT,
    InvalidOrderTransition: status.HTTP_409_CONFLICT,
    InvalidFailureCode: status.HTTP_409_CONFLICT,
    InvalidCancellation: status.HTTP_400_BAD_REQUEST,
    InvalidOrderState: status.HTTP_409_CONFLICT,
}


def raise_api_domain_error(exc: Exception) -> None:
    api_exc = OrderDomainAPIException(str(exc))
    api_exc.status_code = DOMAIN_EXCEPTION_STATUS.get(
        type(exc),
        status.HTTP_409_CONFLICT,
    )
    raise api_exc from exc


class OrderViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer
    http_method_names = ["get", "post", "head", "options"]
    lookup_url_kwarg = "order_pk"

    def get_queryset(self):
        queryset = (
            Order.objects.select_related("customer")
            .prefetch_related(
                "items",
                "items__product",
                "items__product_variant",
            )
            .order_by("-created_at")
        )

        if not self.request.user.is_staff:
            queryset = queryset.filter(customer=self.request.user)

        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset

    def get_object(self):
        queryset = self.get_queryset()
        lookup_value = self.kwargs.get(self.lookup_url_kwarg) or self.kwargs.get(
            self.lookup_field
        )
        return get_object_or_404(queryset, pk=lookup_value)

    def create(self, request, *args, **kwargs):
        order = create_order_draft(
            customer=request.user,
            actor=request.user,
            actor_type=AuditActorType.USER,
            correlation_id=getattr(request, "correlation_id", None),
        )
        return Response(
            self.get_serializer(order).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=False,
        methods=["get"],
        url_path=r"by-reference/(?P<reference>[^/.]+)",
    )
    def by_reference(self, request, reference=None):
        order = get_object_or_404(self.get_queryset(), reference=reference)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["get", "post"], url_path="items")
    def items(self, request, order_pk=None):
        order = self.get_object()

        if request.method == "GET":
            return Response(OrderItemSerializer(get_order_items(order), many=True).data)

        serializer = OrderItemInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            item = add_order_item(
                order=order,
                actor=request.user,
                actor_type=AuditActorType.USER,
                correlation_id=getattr(request, "correlation_id", None),
                **serializer.validated_data,
            )
        except Exception as exc:
            if type(exc) in DOMAIN_EXCEPTION_STATUS:
                raise_api_domain_error(exc)
            raise

        return Response(OrderItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"items/(?P<item_id>[^/.]+)",
    )
    def item_detail(self, request, order_pk=None, item_id=None):
        order = self.get_object()
        item = get_object_or_404(OrderItem, id=item_id, order=order)

        try:
            if request.method == "DELETE":
                remove_order_item(
                    item,
                    actor=request.user,
                    actor_type=AuditActorType.USER,
                    correlation_id=getattr(request, "correlation_id", None),
                )
                return Response(status=status.HTTP_204_NO_CONTENT)

            serializer = OrderItemInputSerializer(
                data=request.data,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)
            item = update_order_item(
                item,
                actor=request.user,
                actor_type=AuditActorType.USER,
                correlation_id=getattr(request, "correlation_id", None),
                **serializer.validated_data,
            )
        except Exception as exc:
            if type(exc) in DOMAIN_EXCEPTION_STATUS:
                raise_api_domain_error(exc)
            raise

        return Response(OrderItemSerializer(item).data)

    @action(detail=True, methods=["patch"], url_path="delivery-location")
    def delivery_location(self, request, order_pk=None):
        order = self.get_object()
        serializer = DeliveryLocationInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            order = set_delivery_location(
                order=order,
                point=serializer.to_point(),
                address_label=serializer.validated_data.get("address_label"),
                actor=request.user,
                actor_type=AuditActorType.USER,
                correlation_id=getattr(request, "correlation_id", None),
            )
        except Exception as exc:
            if type(exc) in DOMAIN_EXCEPTION_STATUS:
                raise_api_domain_error(exc)
            raise

        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["patch"], url_path="delivery-instructions")
    def delivery_instructions(self, request, order_pk=None):
        order = self.get_object()
        serializer = DeliveryInstructionsInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            order = set_delivery_instructions(
                order,
                serializer.validated_data.get("instructions"),
                actor=request.user,
                actor_type=AuditActorType.USER,
                correlation_id=getattr(request, "correlation_id", None),
            )
        except Exception as exc:
            if type(exc) in DOMAIN_EXCEPTION_STATUS:
                raise_api_domain_error(exc)
            raise

        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, order_pk=None):
        order = self.get_object()
        try:
            order = confirm_order(
                order,
                actor=request.user,
                actor_type=AuditActorType.USER,
                correlation_id=getattr(request, "correlation_id", None),
            )
        except Exception as exc:
            if type(exc) in DOMAIN_EXCEPTION_STATUS:
                raise_api_domain_error(exc)
            raise
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, order_pk=None):
        order = self.get_object()
        serializer = CancelOrderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            order = cancel_order(
                order,
                serializer.validated_data["code"],
                reason=serializer.validated_data.get("reason"),
                actor=request.user,
                actor_type=AuditActorType.USER,
                correlation_id=getattr(request, "correlation_id", None),
            )
        except Exception as exc:
            if type(exc) in DOMAIN_EXCEPTION_STATUS:
                raise_api_domain_error(exc)
            raise
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, order_pk=None):
        order = self.get_object()
        try:
            order = retry_failed_order(
                order,
                actor=request.user,
                actor_type=AuditActorType.USER,
                correlation_id=getattr(request, "correlation_id", None),
            )
        except Exception as exc:
            if type(exc) in DOMAIN_EXCEPTION_STATUS:
                raise_api_domain_error(exc)
            raise
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, order_pk=None):
        order = self.get_object()
        events = get_order_audit_history(order)
        page = self.paginate_queryset(events)
        if page is not None:
            serializer = AuditEventSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        return Response(AuditEventSerializer(events, many=True).data)
