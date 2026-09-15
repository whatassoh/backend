from django.db import models

from apps.catalog.domain.enums import (
    CategoryStatus,
    ProductAvailability,
    ProductStatus,
    ProductUnit,
    ProductVariantStatus,
)
from apps.catalog.domain.rules import (
    validate_category_parent,
    validate_price,
    validate_store_product_variant,
)
from apps.catalog.domain.services import (
    build_category_slug,
    normalize_alias,
    normalize_product_name,
    normalize_variant_name,
)
from common.models import BaseModel


class Category(BaseModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=20,
        choices=CategoryStatus.choices,
        default=CategoryStatus.ACTIVE,
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"], name="catalog_category_slug_idx"),
            models.Index(fields=["status"], name="catalog_category_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                name="catalog_category_status_valid",
                condition=models.Q(status__in=CategoryStatus.values),
            ),
            models.CheckConstraint(
                name="catalog_category_not_own_parent",
                condition=~models.Q(id=models.F("parent")),
            ),
        ]

    def clean(self) -> None:
        super().clean()
        validate_category_parent(self.id, self.parent_id)

    def save(self, *args: object, **kwargs: object) -> None:
        self.slug = build_category_slug(self.name, self.slug)
        validate_category_parent(self.id, self.parent_id)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Product(BaseModel):
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, editable=False)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
    )
    default_unit = models.CharField(
        max_length=20,
        choices=ProductUnit.choices,
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ProductStatus.choices,
        default=ProductStatus.ACTIVE,
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["normalized_name"], name="catalog_product_name_idx"),
            models.Index(fields=["status"], name="catalog_product_status_idx"),
            models.Index(fields=["category", "status"], name="catalog_product_cat_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                name="catalog_product_status_valid",
                condition=models.Q(status__in=ProductStatus.values),
            ),
            models.CheckConstraint(
                name="catalog_product_unit_valid",
                condition=(
                    models.Q(default_unit__in=ProductUnit.values)
                    | models.Q(default_unit__isnull=True)
                ),
            ),
            models.UniqueConstraint(
                fields=["category", "normalized_name"],
                name="catalog_product_category_name_unique",
            ),
        ]

    def save(self, *args: object, **kwargs: object) -> None:
        self.normalized_name = normalize_product_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class ProductVariant(BaseModel):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, editable=False)
    attributes = models.JSONField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=ProductVariantStatus.choices,
        default=ProductVariantStatus.ACTIVE,
    )

    class Meta:
        ordering = ["product_id", "name"]
        indexes = [
            models.Index(
                fields=["product", "normalized_name"],
                name="catalog_variant_name_idx",
            ),
            models.Index(fields=["status"], name="catalog_variant_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                name="catalog_variant_status_valid",
                condition=models.Q(status__in=ProductVariantStatus.values),
            ),
            models.UniqueConstraint(
                fields=["product", "normalized_name"],
                name="catalog_variant_product_name_unique",
            ),
        ]

    def save(self, *args: object, **kwargs: object) -> None:
        self.normalized_name = normalize_variant_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.product.name} - {self.name}"


class ProductAlias(BaseModel):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="aliases",
    )
    alias = models.CharField(max_length=255)
    normalized_alias = models.CharField(max_length=255, editable=False)

    class Meta:
        ordering = ["product_id", "normalized_alias"]
        indexes = [
            models.Index(
                fields=["normalized_alias"],
                name="catalog_alias_normalized_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "normalized_alias"],
                name="catalog_alias_product_alias_unique",
            ),
        ]

    def save(self, *args: object, **kwargs: object) -> None:
        self.normalized_alias = normalize_alias(self.alias)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.product.name} - {self.alias}"


class StoreProduct(BaseModel):
    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.CASCADE,
        related_name="catalog_products",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="store_products",
    )
    product_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="store_products",
        blank=True,
        null=True,
    )
    display_name = models.CharField(max_length=255, blank=True, null=True)
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[validate_price],
    )
    currency = models.CharField(max_length=3, default="XAF")
    availability_status = models.CharField(
        max_length=20,
        choices=ProductAvailability.choices,
        default=ProductAvailability.UNKNOWN,
    )

    class Meta:
        ordering = ["store_id", "product_id", "product_variant_id"]
        indexes = [
            models.Index(fields=["store"], name="catalog_sp_store_idx"),
            models.Index(fields=["product"], name="catalog_sp_product_idx"),
            models.Index(fields=["availability_status"], name="catalog_sp_avail_idx"),
            models.Index(
                fields=["store", "availability_status"],
                name="catalog_sp_store_avail_idx",
            ),
            models.Index(
                fields=["product", "availability_status"],
                name="catalog_sp_product_avail_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                name="catalog_sp_availability_valid",
                condition=models.Q(availability_status__in=ProductAvailability.values),
            ),
            models.CheckConstraint(
                name="catalog_sp_price_positive_or_null",
                condition=models.Q(price__gte=0) | models.Q(price__isnull=True),
            ),
            models.UniqueConstraint(
                fields=["store", "product"],
                condition=models.Q(product_variant__isnull=True),
                name="catalog_sp_store_product_null_variant_unique",
            ),
            models.UniqueConstraint(
                fields=["store", "product", "product_variant"],
                condition=models.Q(product_variant__isnull=False),
                name="catalog_sp_store_product_variant_unique",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        validate_store_product_variant(self.product_id, self.product_variant)
        validate_price(self.price)

    def save(self, *args: object, **kwargs: object) -> None:
        validate_store_product_variant(self.product_id, self.product_variant)
        validate_price(self.price)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        variant = f" - {self.product_variant.name}" if self.product_variant else ""
        return f"{self.store.name} - {self.product.name}{variant}"
