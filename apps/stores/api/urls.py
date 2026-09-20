from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.catalog.api.views import StoreProductViewSet
from apps.stores.api.views import StoreViewSet
from apps.stores.api.dashboard_views import (
    DashboardAccessConsumeView,
    DashboardCatalogView,
    DashboardStoreDetailView,
    DashboardStoreOrdersView,
    DashboardOrderPreparationView,
    DashboardStoresView,
)

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
    path("seller-dashboard/access/consume/", DashboardAccessConsumeView.as_view(), name="seller-dashboard-access-consume"),
    path("seller-dashboard/stores/", DashboardStoresView.as_view(), name="seller-dashboard-stores"),
    path("seller-dashboard/stores/<uuid:store_id>/", DashboardStoreDetailView.as_view(), name="seller-dashboard-store-detail"),
    path("seller-dashboard/stores/<uuid:store_id>/catalog/", DashboardCatalogView.as_view(), name="seller-dashboard-catalog"),
    path("seller-dashboard/stores/<uuid:store_id>/orders/", DashboardStoreOrdersView.as_view(), name="seller-dashboard-orders"),
    path("seller-dashboard/orders/<uuid:order_id>/<str:action>/", DashboardOrderPreparationView.as_view(), name="seller-dashboard-order-preparation"),
]
