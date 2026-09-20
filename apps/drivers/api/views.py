from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.drivers.api.serializers import DriverProfileSerializer
from apps.drivers.application.services import (
    activate_driver,
    create_driver_profile,
    deactivate_driver,
    set_driver_busy,
    suspend_driver,
)
from apps.drivers.application.availability import DriverAvailabilityService
from apps.drivers.infrastructure.models import DriverProfile


@extend_schema(
    tags=["Drivers"],
    description=(
        "Profils chauffeur et transitions operationnelles. Toutes les routes "
        "necessitent une authentification."
    ),
)
class DriverProfileViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = DriverProfileSerializer
    queryset = DriverProfile.objects.select_related("user")
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer: DriverProfileSerializer) -> None:
        serializer.instance = create_driver_profile(
            self.request.user,
            **serializer.validated_data,
        )

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        driver = activate_driver(self.get_object())

        return Response(self.get_serializer(driver).data)

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        driver = deactivate_driver(self.get_object())

        return Response(self.get_serializer(driver).data)

    @action(detail=True, methods=["post"], url_path="suspend")
    def suspend(self, request, pk=None):
        driver = suspend_driver(self.get_object())

        return Response(self.get_serializer(driver).data)

    @action(detail=True, methods=["post"], url_path="go-online")
    def go_online(self, request, pk=None):
        driver = DriverAvailabilityService().set_available(self.get_object(), request.user)

        return Response(self.get_serializer(driver).data)

    @action(detail=True, methods=["post"], url_path="go-offline")
    def go_offline(self, request, pk=None):
        driver = DriverAvailabilityService().set_offline(self.get_object(), request.user)

        return Response(self.get_serializer(driver).data)

    @action(detail=True, methods=["post"], url_path="set-busy")
    def set_busy(self, request, pk=None):
        driver = set_driver_busy(self.get_object())

        return Response(self.get_serializer(driver).data, status=status.HTTP_200_OK)
