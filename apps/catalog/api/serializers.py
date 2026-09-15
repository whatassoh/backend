from rest_framework import serializers

from apps.catalog.infrastructure.models import (
    Category,
    Product,
    ProductAlias,
    ProductVariant,
    StoreProduct,
)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "parent",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "name",
            "normalized_name",
            "attributes",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "normalized_name", "created_at", "updated_at"]


class ProductAliasSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductAlias
        fields = [
            "id",
            "alias",
            "normalized_alias",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "normalized_alias", "created_at", "updated_at"]


class ProductSerializer(serializers.ModelSerializer):
    variants = ProductVariantSerializer(many=True, read_only=True)
    aliases = ProductAliasSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "normalized_name",
            "category",
            "default_unit",
            "status",
            "variants",
            "aliases",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "normalized_name", "created_at", "updated_at"]


class StoreProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreProduct
        fields = [
            "id",
            "store",
            "product",
            "product_variant",
            "display_name",
            "price",
            "currency",
            "availability_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "store", "created_at", "updated_at"]
