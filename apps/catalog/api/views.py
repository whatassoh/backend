from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.catalog.api.serializers import (
    CategorySerializer,
    ProductSerializer,
    StoreProductSerializer,
)
from apps.catalog.application.selectors import (
    get_active_categories,
    get_active_products,
    get_store_products,
    search_products,
)
from apps.catalog.application.services import (
    create_category,
    create_product,
)
from apps.catalog.application.seller_catalog import (
    SellerCatalogService,
    SellerCatalogStatus,
)
from apps.catalog.infrastructure.models import Category, Product, StoreProduct
from apps.stores.infrastructure.models import Store


@extend_schema(
    tags=["Catalog / Categories"], description="Categories actives du catalogue."
)
class CategoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CategorySerializer
    queryset = Category.objects.select_related("parent")
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return get_active_categories()

    def perform_create(self, serializer: CategorySerializer) -> None:
        serializer.instance = create_category(**serializer.validated_data)


@extend_schema(
    tags=["Catalog / Products"],
    description=(
        "Produits et variantes. `search` recherche dans les noms et aliases; "
        "`category` filtre par UUID de categorie."
    ),
)
class ProductViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related("category").prefetch_related(
        "variants",
        "aliases",
    )
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        queryset = get_active_products()
        search_query = self.request.query_params.get("search")
        category_id = self.request.query_params.get("category")

        if search_query:
            queryset = search_products(search_query)

        if category_id:
            queryset = queryset.filter(category_id=category_id)

        return queryset

    def perform_create(self, serializer: ProductSerializer) -> None:
        serializer.instance = create_product(**serializer.validated_data)


@extend_schema(
    tags=["Catalog / Store offers"],
    description=(
        "Offres de catalogue d'une boutique. `availability_status` n'est pas "
        "une garantie de stock instantane."
    ),
)
class StoreProductViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = StoreProductSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_store(self) -> Store:
        return get_object_or_404(Store, id=self.kwargs["store_pk"])

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return StoreProduct.objects.none()
        return get_store_products(self.get_store())

    def perform_create(self, serializer: StoreProductSerializer) -> None:
        store = self.get_store()
        data = serializer.validated_data
        result = SellerCatalogService().add_product(
            actor=self.request.user,
            store=store,
            product_id=data["product"].id,
            variant_id=(data["product_variant"].id if data.get("product_variant") else None),
            price=data.get("price"),
            currency=data.get("currency", "XAF"),
            availability=data.get("availability_status", "UNKNOWN"),
            display_name=data.get("display_name"),
            correlation_id=self.request.headers.get("X-Correlation-ID"),
        )
        if result.status == SellerCatalogStatus.FORBIDDEN:
            self.permission_denied(self.request)
        if result.status != SellerCatalogStatus.SUCCESS:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"detail": result.status.value})
        serializer.instance = result.store_product

    def partial_update(self, request, *args, **kwargs):
        store = self.get_store()

        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if set(data) - {"price", "currency", "availability_status"}:
            return Response({"detail": "Only price and availability can be updated here."}, status=status.HTTP_400_BAD_REQUEST)
        service = SellerCatalogService()
        result = None
        if "price" in data or "currency" in data:
            result = service.update_price(
                actor=request.user, store_product_id=instance.id,
                price=data.get("price", instance.price),
                currency=data.get("currency", instance.currency),
                correlation_id=request.headers.get("X-Correlation-ID"),
            )
        if "availability_status" in data:
            result = service.set_availability(
                actor=request.user, store_product_id=instance.id,
                availability=data["availability_status"],
                correlation_id=request.headers.get("X-Correlation-ID"),
            )
        if result is None:
            return Response(self.get_serializer(instance).data)
        if result.status == SellerCatalogStatus.FORBIDDEN:
            self.permission_denied(request)
        if result.status != SellerCatalogStatus.SUCCESS:
            return Response({"detail": result.status.value}, status=status.HTTP_400_BAD_REQUEST)
        store_product = result.store_product

        return Response(self.get_serializer(store_product).data)

    def destroy(self, request, *args, **kwargs):
        store = self.get_store()

        result = SellerCatalogService().remove_product(
            actor=request.user,
            store_product_id=self.get_object().id,
            correlation_id=request.headers.get("X-Correlation-ID"),
        )
        if result.status == SellerCatalogStatus.FORBIDDEN:
            self.permission_denied(request)
        if result.status != SellerCatalogStatus.SUCCESS:
            return Response({"detail": result.status.value}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)
