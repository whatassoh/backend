from django.contrib import admin

from apps.catalog.infrastructure.models import (
    Category,
    Product,
    ProductAlias,
    ProductVariant,
    StoreProduct,
)


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    readonly_fields = ["normalized_name", "created_at", "updated_at"]


class ProductAliasInline(admin.TabularInline):
    model = ProductAlias
    extra = 0
    readonly_fields = ["normalized_alias", "created_at", "updated_at"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "parent", "status", "created_at"]
    list_filter = ["status", "parent"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    autocomplete_fields = ["parent"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductVariantInline, ProductAliasInline]
    list_display = [
        "name",
        "normalized_name",
        "category",
        "status",
        "default_unit",
        "created_at",
    ]
    list_filter = ["status", "default_unit", "category"]
    search_fields = ["name", "normalized_name", "aliases__alias"]
    autocomplete_fields = ["category"]
    readonly_fields = ["id", "normalized_name", "created_at", "updated_at"]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ["product", "name", "normalized_name", "status", "created_at"]
    list_filter = ["status", "product__category"]
    search_fields = ["product__name", "name", "normalized_name"]
    autocomplete_fields = ["product"]
    readonly_fields = ["id", "normalized_name", "created_at", "updated_at"]


@admin.register(ProductAlias)
class ProductAliasAdmin(admin.ModelAdmin):
    list_display = ["product", "alias", "normalized_alias", "created_at"]
    search_fields = ["product__name", "alias", "normalized_alias"]
    autocomplete_fields = ["product"]
    readonly_fields = ["id", "normalized_alias", "created_at", "updated_at"]


@admin.register(StoreProduct)
class StoreProductAdmin(admin.ModelAdmin):
    list_display = [
        "store",
        "product",
        "product_variant",
        "display_name",
        "availability_status",
        "price",
        "currency",
        "updated_at",
    ]
    list_filter = [
        "availability_status",
        "currency",
        "store",
        "product__category",
    ]
    search_fields = [
        "store__name",
        "product__name",
        "product_variant__name",
        "display_name",
    ]
    autocomplete_fields = ["store", "product", "product_variant"]
    readonly_fields = ["id", "created_at", "updated_at"]
