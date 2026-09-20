from django.contrib.auth import login
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.api.serializers import StoreProductSerializer
from apps.catalog.application.seller_catalog import SellerCatalogService, SellerCatalogStatus
from apps.orders.api.serializers import OrderSerializer
from apps.orders.infrastructure.models import Order
from apps.stores.api.serializers import StoreSerializer
from apps.stores.application.dashboard_access import SellerDashboardAccessService
from apps.stores.application.preparation import StorePreparationResult, StorePreparationService
from apps.stores.application.permissions import user_has_store_role
from apps.stores.domain.enums import StoreMembershipRole, StoreMembershipStatus
from apps.stores.infrastructure.models import Store, StoreMembership


class DashboardAccessConsumeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        result = SellerDashboardAccessService().consume(
            raw_token=request.data.get("token"),
            correlation_id=getattr(request, "correlation_id", None),
        )
        if result.status != "CONSUMED":
            return Response({"detail": "Access link is invalid or expired."}, status=400)
        login(request, result.user)
        return Response({"redirect_url": "/seller-dashboard/"})


class DashboardStoresView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        stores = Store.objects.filter(
            memberships__user=request.user,
            memberships__status=StoreMembershipStatus.ACTIVE,
        ).distinct().order_by("name")
        return Response(StoreSerializer(stores, many=True).data)


class DashboardStoreDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_id):
        store = get_object_or_404(Store, pk=store_id)
        if not user_has_store_role(request.user, store, list(StoreMembershipRole.values)):
            return Response({"detail": "Forbidden."}, status=403)
        return Response(StoreSerializer(store).data)


class DashboardCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, store_id):
        store = get_object_or_404(Store, pk=store_id)
        result = SellerCatalogService().list_catalog(actor=request.user, store=store)
        if result.status != SellerCatalogStatus.SUCCESS:
            return Response({"detail": "Forbidden."}, status=403)
        return Response(StoreProductSerializer(result.items, many=True).data)


class DashboardStoreOrdersView(APIView):
    """Read-only offer/order visibility; preparation transitions remain 8.6."""
    permission_classes = [IsAuthenticated]

    def get(self, request, store_id):
        store = get_object_or_404(Store, pk=store_id)
        if not user_has_store_role(request.user, store, list(StoreMembershipRole.values)):
            return Response({"detail": "Forbidden."}, status=403)
        orders = Order.objects.filter(store_offers__store=store).distinct().select_related("customer").prefetch_related("items", "items__product", "items__product_variant")
        return Response(OrderSerializer(orders, many=True).data)


class DashboardOrderPreparationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id, action):
        if action not in {"start-preparation", "mark-ready"}:
            return Response({"detail": "Unknown action."}, status=404)
        service = StorePreparationService()
        operation = service.start_preparation if action == "start-preparation" else service.mark_ready_for_pickup
        result = operation(
            actor=request.user, order_id=order_id,
            correlation_id=request.headers.get("X-Correlation-ID"),
        )
        if result == StorePreparationResult.NOT_AUTHORIZED:
            return Response({"detail": "Forbidden."}, status=403)
        if result == StorePreparationResult.NOT_AVAILABLE:
            return Response({"detail": "Order is not in a valid state."}, status=409)
        return Response({"status": result})
