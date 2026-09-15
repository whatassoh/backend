import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.catalog.application.selectors import (
    get_active_categories,
    get_active_products,
    get_category_children,
    get_product_aliases,
    get_product_variants,
    get_products_by_category,
    search_products,
)
from apps.catalog.application.services import (
    add_product_alias,
    create_category,
    create_product,
    create_product_variant,
    deactivate_category,
    deactivate_product,
    deactivate_product_variant,
    remove_product_alias,
)
from apps.catalog.domain.enums import (
    CategoryStatus,
    ProductStatus,
    ProductUnit,
    ProductVariantStatus,
)
from apps.catalog.infrastructure.models import (
    Category,
    ProductAlias,
    ProductVariant,
)


@pytest.mark.django_db
def test_create_category():
    category = create_category(name="Alimentation")

    assert category.id
    assert category.name == "Alimentation"
    assert category.slug == "alimentation"
    assert category.parent is None
    assert category.status == CategoryStatus.ACTIVE


@pytest.mark.django_db
def test_create_child_category():
    parent = create_category(name="Alimentation")

    child = create_category(name="Riz et céréales", parent=parent)

    assert child.parent == parent
    assert list(get_category_children(parent)) == [child]


@pytest.mark.django_db
def test_category_cannot_be_its_own_parent():
    category = create_category(name="Alimentation")
    category.parent = category

    with pytest.raises(ValidationError):
        category.save()


@pytest.mark.django_db
def test_category_status_active_inactive():
    active_category = create_category(name="Alimentation")
    inactive_category = create_category(
        name="Hygiène",
        status=CategoryStatus.INACTIVE,
    )

    assert active_category in get_active_categories()
    assert inactive_category not in get_active_categories()


@pytest.mark.django_db
def test_deactivate_category_keeps_it_in_database():
    category = create_category(name="Alimentation")

    deactivate_category(category)
    category.refresh_from_db()

    assert category.status == CategoryStatus.INACTIVE
    assert Category.objects.filter(id=category.id).exists()


@pytest.mark.django_db
def test_create_product_sets_normalized_name_and_category():
    category = create_category(name="Alimentation")

    product = create_product(
        name="Riz Parfumé",
        category=category,
        default_unit=ProductUnit.KG,
    )

    assert product.category == category
    assert product.normalized_name == "riz parfume"
    assert product.default_unit == ProductUnit.KG
    assert product.status == ProductStatus.ACTIVE


@pytest.mark.django_db
def test_product_default_unit_can_be_null():
    category = create_category(name="Alimentation")

    product = create_product(name="Riz", category=category)

    assert product.default_unit is None


@pytest.mark.django_db
def test_deactivate_product_keeps_it_in_database():
    category = create_category(name="Alimentation")
    product = create_product(name="Riz", category=category)

    deactivate_product(product)
    product.refresh_from_db()

    assert product.status == ProductStatus.INACTIVE
    assert product not in get_active_products()


@pytest.mark.django_db
def test_product_name_unique_per_category_after_normalization():
    category = create_category(name="Alimentation")
    create_product(name="Huile Mayor", category=category)

    with pytest.raises(IntegrityError):
        create_product(name=" huile   mayor ", category=category)


@pytest.mark.django_db
def test_search_products_by_name():
    category = create_category(name="Alimentation")
    product = create_product(name="Huile Mayor", category=category)

    assert list(search_products("mayor")) == [product]


@pytest.mark.django_db
def test_get_products_by_category():
    food = create_category(name="Alimentation")
    hygiene = create_category(name="Hygiène")
    rice = create_product(name="Riz", category=food)
    create_product(name="Savon Azur", category=hygiene)

    assert list(get_products_by_category(food)) == [rice]


@pytest.mark.django_db
def test_create_product_variant():
    category = create_category(name="Alimentation")
    product = create_product(name="Huile Mayor", category=category)

    variant = create_product_variant(
        product=product,
        name="1 L",
        attributes={"volume_ml": 1000},
    )

    assert variant.product == product
    assert variant.normalized_name == "1 l"
    assert variant.attributes == {"volume_ml": 1000}
    assert variant.status == ProductVariantStatus.ACTIVE


@pytest.mark.django_db
def test_product_without_variant_is_valid():
    category = create_category(name="Alimentation")
    product = create_product(name="Riz", category=category)

    assert list(get_product_variants(product)) == []


@pytest.mark.django_db
def test_multiple_variants_on_product():
    category = create_category(name="Alimentation")
    product = create_product(name="Huile Mayor", category=category)

    create_product_variant(product=product, name="500 ml")
    create_product_variant(product=product, name="1 L")

    assert get_product_variants(product).count() == 2


@pytest.mark.django_db
def test_deactivate_product_variant():
    category = create_category(name="Alimentation")
    product = create_product(name="Huile Mayor", category=category)
    variant = create_product_variant(product=product, name="1 L")

    deactivate_product_variant(variant)
    variant.refresh_from_db()

    assert variant.status == ProductVariantStatus.INACTIVE


@pytest.mark.django_db
def test_variant_name_unique_per_product_after_normalization():
    category = create_category(name="Alimentation")
    product = create_product(name="Huile Mayor", category=category)
    create_product_variant(product=product, name="1 L")

    with pytest.raises(IntegrityError):
        ProductVariant.objects.create(product=product, name=" 1   l ")


@pytest.mark.django_db
def test_product_without_alias_is_valid():
    category = create_category(name="Alimentation")
    product = create_product(name="Riz", category=category)

    assert list(get_product_aliases(product)) == []


@pytest.mark.django_db
def test_add_product_alias_normalizes_alias():
    category = create_category(name="Alimentation")
    product = create_product(name="Huile Mayor", category=category)

    alias = add_product_alias(product=product, alias=" MAYOR ")

    assert alias.alias == " MAYOR "
    assert alias.normalized_alias == "mayor"


@pytest.mark.django_db
def test_alias_unique_per_product_after_normalization():
    category = create_category(name="Alimentation")
    product = create_product(name="Huile Mayor", category=category)
    add_product_alias(product=product, alias="Mayor")

    with pytest.raises(IntegrityError):
        ProductAlias.objects.create(product=product, alias=" mayor ")


@pytest.mark.django_db
def test_search_products_by_alias():
    category = create_category(name="Alimentation")
    product = create_product(name="Huile Mayor", category=category)
    add_product_alias(product=product, alias="Mayor")

    assert list(search_products("mayor")) == [product]


@pytest.mark.django_db
def test_remove_product_alias():
    category = create_category(name="Alimentation")
    product = create_product(name="Huile Mayor", category=category)
    alias = add_product_alias(product=product, alias="Mayor")

    deleted_count = remove_product_alias(alias)

    assert deleted_count == 1
    assert list(get_product_aliases(product)) == []
