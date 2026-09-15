from rest_framework import serializers

from apps.drivers.infrastructure.models import DriverProfile


class DriverProfileSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = DriverProfile
        fields = [
            "id",
            "user",
            "status",
            "availability_status",
            "vehicle_type",
            "vehicle_label",
            "license_plate",
            "service_radius_meters",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "status",
            "availability_status",
            "created_at",
            "updated_at",
        ]
