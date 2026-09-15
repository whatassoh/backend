from django.contrib import admin

from apps.orders.infrastructure.models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ["product", "product_variant"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = [OrderItemInline]
    list_display = [
        "reference",
        "customer",
        "status",
        "created_at",
        "confirmed_at",
        "failed_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["reference", "customer__whatsapp_phone"]
    autocomplete_fields = ["customer"]
    readonly_fields = [
        "id",
        "reference",
        "status",
        "confirmed_at",
        "cancelled_at",
        "completed_at",
        "failed_at",
        "cancellation_code",
        "cancellation_reason",
        "failure_code",
        "failure_reason",
        "failed_from_status",
        "created_at",
        "updated_at",
    ]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = [
        "order",
        "requested_name",
        "quantity",
        "unit",
        "product",
        "product_variant",
        "resolution_status",
    ]
    list_filter = ["unit", "resolution_status"]
    search_fields = [
        "order__reference",
        "requested_name",
        "product__name",
        "product_variant__name",
    ]
    autocomplete_fields = ["order", "product", "product_variant"]
    readonly_fields = ["id", "created_at", "updated_at"]
