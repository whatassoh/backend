from django.core.exceptions import ValidationError
from django.utils.text import slugify


def normalize_catalog_text(value: str) -> str:
    return " ".join(slugify(value, allow_unicode=False).replace("-", " ").split())


def make_category_slug(name: str) -> str:
    return slugify(name, allow_unicode=False)


def validate_category_parent(category_id: object, parent_id: object) -> None:
    if category_id is not None and category_id == parent_id:
        raise ValidationError("A category cannot be its own parent.")


def validate_store_product_variant(product_id: object, variant: object | None) -> None:
    if variant is not None and variant.product_id != product_id:
        raise ValidationError("Product variant must belong to the selected product.")


def validate_price(value: object | None) -> None:
    if value is not None and value < 0:
        raise ValidationError("Price must be greater than or equal to zero.")
