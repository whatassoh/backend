from django.urls import include, path

urlpatterns = [
    path("", include("apps.identity.api.urls")),
    path("", include("apps.stores.api.urls")),
    path("", include("apps.drivers.api.urls")),
    path("", include("apps.catalog.api.urls")),
    path("", include("apps.orders.api.urls")),
]
