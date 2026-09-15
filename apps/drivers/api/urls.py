from rest_framework.routers import DefaultRouter

from apps.drivers.api.views import DriverProfileViewSet

router = DefaultRouter()
router.register("drivers", DriverProfileViewSet, basename="drivers")

urlpatterns = router.urls
