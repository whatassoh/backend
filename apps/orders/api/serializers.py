from decimal import Decimal

from django.contrib.gis.geos import Point
from rest_framework import serializers

from apps.audit.infrastructure.models import AuditEvent
from apps.catalog.infrastructure.models import Product, ProductVariant
from apps.orders.infrastructure.models import Order, OrderItem


class ProductSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name"]


class ProductVariantSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ["id", "name"]


class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductSummarySerializer(read_only=True)
    product_variant = ProductVariantSummarySerializer(read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "requested_name",
            "quantity",
            "unit",
            "notes",
            "product",
            "product_variant",
            "resolution_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class OrderItemInputSerializer(serializers.Serializer):
    requested_name = serializers.CharField(max_length=255)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    unit = serializers.CharField(max_length=20)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    product_id = serializers.UUIDField(required=False, allow_null=True)
    product_variant_id = serializers.UUIDField(required=False, allow_null=True)
    resolution_status = serializers.CharField(required=False, default="UNMATCHED")

    def validate_quantity(self, quantity: Decimal) -> Decimal:
        if quantity <= 0:
            raise serializers.ValidationError("Quantity must be strictly positive.")
        return quantity

    def validate(self, attrs):
        product_id = attrs.get("product_id")
        variant_id = attrs.get("product_variant_id")

        if "product_id" in attrs:
            attrs["product"] = None
            if product_id is not None:
                try:
                    attrs["product"] = Product.objects.get(id=product_id)
                except Product.DoesNotExist as exc:
                    raise serializers.ValidationError(
                        {"product_id": "Product does not exist."}
                    ) from exc
        if "product_variant_id" in attrs:
            attrs["product_variant"] = None
            if variant_id is not None:
                try:
                    attrs["product_variant"] = ProductVariant.objects.get(id=variant_id)
                except ProductVariant.DoesNotExist as exc:
                    raise serializers.ValidationError(
                        {"product_variant_id": "Product variant does not exist."}
                    ) from exc

        attrs.pop("product_id", None)
        attrs.pop("product_variant_id", None)
        return attrs


class DeliveryLocationInputSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    address_label = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=255,
    )

    def to_point(self) -> Point:
        return Point(
            self.validated_data["longitude"],
            self.validated_data["latitude"],
            srid=4326,
        )


class DeliveryInstructionsInputSerializer(serializers.Serializer):
    instructions = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )


class CancelOrderInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=30)
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CustomerSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    whatsapp_phone = serializers.CharField()


class OrderSerializer(serializers.ModelSerializer):
    customer = CustomerSummarySerializer(read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    delivery_location = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "reference",
            "customer",
            "status",
            "items",
            "delivery_location",
            "delivery_address_label",
            "delivery_instructions",
            "currency",
            "confirmed_at",
            "cancelled_at",
            "completed_at",
            "failed_at",
            "cancellation_code",
            "failure_code",
            "failed_from_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_delivery_location(self, order: Order) -> dict | None:
        if order.delivery_location is None:
            return None
        return {
            "longitude": order.delivery_location.x,
            "latitude": order.delivery_location.y,
        }


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "created_at",
            "event_type",
            "actor_type",
            "previous_state",
            "new_state",
            "correlation_id",
            "metadata",
        ]
        read_only_fields = fields
