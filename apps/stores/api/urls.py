from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.catalog.api.views import StoreProductViewSet
from apps.stores.api.views import StoreViewSet

router = DefaultRouter()
router.register("stores", StoreViewSet, basename="stores")

store_products = StoreProductViewSet.as_view(
    {
        "get": "list",
        "post": "create",
    }
)
store_product_detail = StoreProductViewSet.as_view(
    {
        "get": "retrieve",
        "patch": "partial_update",
        "delete": "destroy",
    }
)

urlpatterns = [
    *router.urls,
    path(
        "stores/<uuid:store_pk>/products/",
        store_products,
        name="store-products",
    ),
    path(
        "stores/<uuid:store_pk>/products/<uuid:pk>/",
        store_product_detail,
        name="store-product-detail",
    ),
]
