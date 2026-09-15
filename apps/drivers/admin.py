from django.contrib import admin

from apps.drivers.infrastructure.models import DriverProfile


@admin.register(DriverProfile)
class DriverProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "status",
        "availability_status",
        "vehicle_type",
        "license_plate",
        "service_radius_meters",
        "created_at",
    ]
    list_filter = [
        "status",
        "availability_status",
        "vehicle_type",
    ]
    search_fields = [
        "user__whatsapp_phone",
        "user__first_name",
        "user__last_name",
        "license_plate",
        "vehicle_label",
    ]
    autocomplete_fields = ["user"]
    readonly_fields = ["id", "created_at", "updated_at"]
    fieldsets = [
        (
            None,
            {
                "fields": [
                    "id",
                    "user",
                    "status",
                    "availability_status",
                ],
            },
        ),
        (
            "Vehicle",
            {
                "fields": [
                    "vehicle_type",
                    "vehicle_label",
                    "license_plate",
                ],
            },
        ),
        (
            "Operations",
            {
                "fields": [
                    "service_radius_meters",
                ],
            },
        ),
        (
            "System",
            {
                "fields": [
                    "created_at",
                    "updated_at",
                ],
            },
        ),
    ]
