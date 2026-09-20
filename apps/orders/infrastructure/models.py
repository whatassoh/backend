from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.core.validators import RegexValidator
from django.db import models

from apps.catalog.domain.enums import ProductUnit
from apps.catalog.infrastructure.models import Product, ProductVariant
from apps.orders.domain.enums import (
    OrderCancellationCode,
    OrderFailureCode,
    OrderItemResolutionStatus,
    OrderStatus,
)
from apps.orders.domain.rules import (
    validate_order_item_product_variant,
    validate_order_item_resolution,
    validate_positive_quantity,
)
from common.models import BaseModel


class Order(BaseModel):
    reference = models.CharField(
        max_length=15,
        unique=True,
        editable=False,
        validators=[
            RegexValidator(
                regex=r"^ORD-\d{6}-[A-Z2-9]{4}$",
                message="Order reference must follow ORD-YYMMDD-XXXX.",
            )
        ],
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    status = models.CharField(
        max_length=30,
        choices=OrderStatus.choices,
        default=OrderStatus.DRAFT,
    )
    delivery_location = gis_models.PointField(
        geography=True,
        srid=4326,
        blank=True,
        null=True,
    )
    delivery_address_label = models.CharField(max_length=255, blank=True, null=True)
    delivery_instructions = models.TextField(blank=True, null=True)
    currency = models.CharField(max_length=3, default="XAF")
    confirmed_at = models.DateTimeField(blank=True, null=True)
    picked_up_at = models.DateTimeField(blank=True, null=True)
    delivery_started_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    cancellation_code = models.CharField(
        max_length=30,
        choices=OrderCancellationCode.choices,
        blank=True,
        null=True,
    )
    cancellation_reason = models.TextField(blank=True, null=True)
    failure_code = models.CharField(
        max_length=40,
        choices=OrderFailureCode.choices,
        blank=True,
        null=True,
    )
    failure_reason = models.TextField(blank=True, null=True)
    failed_from_status = models.CharField(
        max_length=30,
        choices=OrderStatus.choices,
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["customer"], name="orders_order_customer_idx"),
            models.Index(fields=["status"], name="orders_order_status_idx"),
            models.Index(
                fields=["customer", "status"], name="orders_order_cust_status_idx"
            ),
            models.Index(fields=["created_at"], name="orders_order_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                name="orders_order_status_valid",
                condition=models.Q(status__in=OrderStatus.values),
            ),
            models.CheckConstraint(
                name="orders_order_cancel_code_valid",
                condition=(
                    models.Q(cancellation_code__in=OrderCancellationCode.values)
                    | models.Q(cancellation_code__isnull=True)
                ),
            ),
            models.CheckConstraint(
                name="orders_order_failure_code_valid",
                condition=(
                    models.Q(failure_code__in=OrderFailureCode.values)
                    | models.Q(failure_code__isnull=True)
                ),
            ),
            models.CheckConstraint(
                name="orders_order_failed_from_valid",
                condition=(
                    models.Q(failed_from_status__in=OrderStatus.values)
                    | models.Q(failed_from_status__isnull=True)
                ),
            ),
        ]

    def __str__(self) -> str:
        return self.reference


class OrderItem(BaseModel):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    requested_name = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=20, choices=ProductUnit.choices)
    notes = models.TextField(blank=True, null=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_items",
        blank=True,
        null=True,
    )
    product_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="order_items",
        blank=True,
        null=True,
    )
    resolution_status = models.CharField(
        max_length=20,
        choices=OrderItemResolutionStatus.choices,
        default=OrderItemResolutionStatus.UNMATCHED,
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["order"], name="orders_item_order_idx"),
            models.Index(fields=["product"], name="orders_item_product_idx"),
            models.Index(
                fields=["resolution_status"], name="orders_item_res_status_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                name="orders_item_quantity_positive",
                condition=models.Q(quantity__gt=0),
            ),
            models.CheckConstraint(
                name="orders_item_unit_valid",
                condition=models.Q(unit__in=ProductUnit.values),
            ),
            models.CheckConstraint(
                name="orders_item_resolution_valid",
                condition=models.Q(
                    resolution_status__in=OrderItemResolutionStatus.values
                ),
            ),
            models.CheckConstraint(
                name="orders_item_matched_has_product",
                condition=(
                    ~models.Q(resolution_status=OrderItemResolutionStatus.MATCHED)
                    | models.Q(product__isnull=False)
                ),
            ),
            models.CheckConstraint(
                name="orders_item_unmatched_no_product",
                condition=(
                    ~models.Q(resolution_status=OrderItemResolutionStatus.UNMATCHED)
                    | models.Q(product__isnull=True)
                ),
            ),
            models.CheckConstraint(
                name="orders_item_variant_has_product",
                condition=(
                    models.Q(product_variant__isnull=True)
                    | models.Q(product__isnull=False)
                ),
            ),
        ]

    def clean(self) -> None:
        super().clean()
        validate_positive_quantity(self.quantity)
        validate_order_item_product_variant(self.product_id, self.product_variant)
        validate_order_item_resolution(self.resolution_status, self.product_id)

    def save(self, *args: object, **kwargs: object) -> None:
        validate_positive_quantity(self.quantity)
        validate_order_item_product_variant(self.product_id, self.product_variant)
        validate_order_item_resolution(self.resolution_status, self.product_id)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.order.reference} - {self.requested_name}"
