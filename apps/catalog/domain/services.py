from apps.catalog.domain.rules import make_category_slug, normalize_catalog_text


def normalize_product_name(name: str) -> str:
    return normalize_catalog_text(name)


def normalize_variant_name(name: str) -> str:
    return normalize_catalog_text(name)


def normalize_alias(alias: str) -> str:
    return normalize_catalog_text(alias)


def build_category_slug(name: str, slug: str | None = None) -> str:
    return slug or make_category_slug(name)
