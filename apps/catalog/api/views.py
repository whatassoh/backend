from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.catalog.api.serializers import (
    CategorySerializer,
    ProductSerializer,
    StoreProductSerializer,
)
from apps.catalog.application.permissions import can_manage_store_catalog
from apps.catalog.application.selectors import (
    get_active_categories,
    get_active_products,
    get_store_products,
    search_products,
)
from apps.catalog.application.services import (
    add_product_to_store,
    create_category,
    create_product,
    remove_product_from_store,
    update_store_product,
)
from apps.catalog.infrastructure.models import Category, Product
from apps.stores.infrastructure.models import Store


class CategoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CategorySerializer
    queryset = Category.objects.select_related("parent")
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return get_active_categories()

    def perform_create(self, serializer: CategorySerializer) -> None:
        serializer.instance = create_category(**serializer.validated_data)


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


class StoreProductViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = StoreProductSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_store(self) -> Store:
        return get_object_or_404(Store, id=self.kwargs["store_pk"])

    def get_queryset(self):
        return get_store_products(self.get_store())

    def perform_create(self, serializer: StoreProductSerializer) -> None:
        store = self.get_store()

        if not can_manage_store_catalog(self.request.user, store):
            self.permission_denied(self.request)

        serializer.instance = add_product_to_store(
            store=store,
            **serializer.validated_data,
        )

    def partial_update(self, request, *args, **kwargs):
        store = self.get_store()

        if not can_manage_store_catalog(request.user, store):
            self.permission_denied(request)

        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        store_product = update_store_product(instance, **serializer.validated_data)

        return Response(self.get_serializer(store_product).data)

    def destroy(self, request, *args, **kwargs):
        store = self.get_store()

        if not can_manage_store_catalog(request.user, store):
            self.permission_denied(request)

        remove_product_from_store(self.get_object())

        return Response(status=status.HTTP_204_NO_CONTENT)
