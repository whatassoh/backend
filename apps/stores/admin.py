from django.contrib import admin

from apps.catalog.infrastructure.models import StoreProduct
from apps.stores.infrastructure.models import Store, StoreMembership


class StoreMembershipInline(admin.TabularInline):
    model = StoreMembership
    extra = 0
    autocomplete_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]


class StoreProductInline(admin.TabularInline):
    model = StoreProduct
    extra = 0
    autocomplete_fields = ["product", "product_variant"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    inlines = [StoreMembershipInline, StoreProductInline]
    list_display = [
        "name",
        "phone",
        "status",
        "is_open",
        "city",
        "neighborhood",
        "service_radius_meters",
        "location_display",
    ]
    list_filter = ["status", "is_open", "city"]
    search_fields = ["name", "phone", "city", "neighborhood"]
    readonly_fields = ["id", "created_at", "updated_at", "location_display"]
    fieldsets = [
        (
            None,
            {
                "fields": [
                    "id",
                    "name",
                    "description",
                    "status",
                    "is_open",
                ],
            },
        ),
        (
            "Contact",
            {
                "fields": [
                    "phone",
                    "address_label",
                    "city",
                    "neighborhood",
                ],
            },
        ),
        (
            "Geography",
            {
                "fields": [
                    "location",
                    "location_display",
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

    @admin.display(description="Location")
    def location_display(self, store: Store) -> str:
        return f"{store.location.y:.6f}, {store.location.x:.6f}"


@admin.register(StoreMembership)
class StoreMembershipAdmin(admin.ModelAdmin):
    list_display = ["store", "user", "role", "status", "created_at"]
    list_filter = ["role", "status", "store__city"]
    search_fields = [
        "store__name",
        "user__whatsapp_phone",
        "user__first_name",
        "user__last_name",
    ]
    autocomplete_fields = ["store", "user"]
    readonly_fields = ["created_at", "updated_at"]
