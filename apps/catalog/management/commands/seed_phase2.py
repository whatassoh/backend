from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand

from apps.catalog.application.services import (
    add_product_alias,
    add_product_to_store,
    create_category,
    create_product,
    create_product_variant,
    set_product_availability,
    set_store_product_price,
)
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.catalog.domain.services import (
    build_category_slug,
    normalize_alias,
    normalize_product_name,
    normalize_variant_name,
)
from apps.catalog.infrastructure.models import (
    Category,
    Product,
    ProductAlias,
    ProductVariant,
    StoreProduct,
)
from apps.drivers.application.services import (
    activate_driver,
    create_driver_profile,
    set_driver_available,
    set_driver_busy,
)
from apps.drivers.domain.enums import DriverAvailability, VehicleType
from apps.drivers.infrastructure.models import DriverProfile
from apps.identity.application.services import (
    assign_role,
    create_user_for_telegram,
    get_or_create_user_by_whatsapp,
)
from apps.identity.domain.enums import UserRoleType
from apps.stores.application.services import add_store_member, create_store
from apps.stores.domain.enums import StoreMembershipRole
from apps.stores.domain.services import make_store_point
from apps.stores.infrastructure.models import Store


from apps.messaging.application.services import (
    get_or_create_channel_identity,
)

from apps.messaging.domain.enums import MessagingChannel
from apps.messaging.infrastructure.models import ChannelIdentity


class Command(BaseCommand):
    help = "Seed reproducible Phase 2 demo data."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("Seeding Phase 2 demo data...")

        users = self._seed_users()
        categories = self._seed_categories()
        products = self._seed_products(categories)
        variants = self._seed_variants(products)
        self._seed_aliases(products)
        stores = self._seed_stores(users)
        self._seed_drivers(users)
        self._seed_store_products(stores, products, variants)

        self.stdout.write(self.style.SUCCESS("Phase 2 demo data seeded."))

    def _seed_users(self) -> dict[str, object]:
        specs = [
            ("owner_a", "+237674242450", "Louis", "David"),
            ("owner_b", "+237696767542", "Jacob", "Brayan"),
            ("manager_a", "+237699000003", "Carine", "Manager"),
            ("agent_a", "+237699000004", "David", "Agent"),
            ("driver_a", "+237699000005", "Estelle", "Driver"),
            ("driver_b", "+237699000006", "Fabrice", "Driver"),
            ("driver_c", "+237699000007", "Grace", "Driver"),
            ("driver_d", "+237699000008", "Hugo", "Driver"),
            ("driver_e", "+237699000009", "Ines", "Driver"),
            ("client_a", "+237699000010", "Joelle", "Client"),
        ]
        users = {}

        for key, phone, first_name, last_name in specs:
            user, _ = get_or_create_user_by_whatsapp(
                phone,
                first_name=first_name,
                last_name=last_name,
            )
            users[key] = user

        assign_role(users["owner_a"], UserRoleType.STORE_OWNER)
        assign_role(users["owner_b"], UserRoleType.STORE_OWNER)
        assign_role(users["manager_a"], UserRoleType.STORE_MANAGER)
        assign_role(users["agent_a"], UserRoleType.STORE_AGENT)
        assign_role(users["client_a"], UserRoleType.CLIENT)

        return users

    def _seed_categories(self) -> dict[str, Category]:
        specs = [
            ("food", "Alimentation", None),
            ("rice", "Riz et céréales", "food"),
            ("oils", "Huiles", "food"),
            ("hygiene", "Hygiène", None),
            ("soaps", "Savons", "hygiene"),
            ("cleaning", "Détergents", "hygiene"),
            ("drinks", "Boissons", None),
            ("baby", "Bébé", None),
        ]
        categories = {}

        for key, name, parent_key in specs:
            parent = categories.get(parent_key)
            slug = build_category_slug(name)
            category = Category.objects.filter(slug=slug).first()
            if category is None:
                category = create_category(name=name, parent=parent)
            categories[key] = category

        return categories

    def _seed_products(self, categories: dict[str, Category]) -> dict[str, Product]:
        specs = [
            ("riz", "Riz", "rice", ProductUnit.KG),
            ("riz_parfume", "Riz parfumé", "rice", ProductUnit.KG),
            ("spaghetti", "Spaghetti", "rice", ProductUnit.PACK),
            ("huile_mayor", "Huile Mayor", "oils", ProductUnit.BOTTLE),
            ("huile_palmiste", "Huile de palme", "oils", ProductUnit.L),
            ("sucre", "Sucre", "food", ProductUnit.KG),
            ("sel", "Sel", "food", ProductUnit.PACK),
            ("lait", "Lait", "food", ProductUnit.BOTTLE),
            ("tomate", "Tomate concentrée", "food", ProductUnit.UNIT),
            ("cube", "Cube d'assaisonnement", "food", ProductUnit.PACK),
            ("savon_azur", "Savon Azur", "soaps", ProductUnit.UNIT),
            ("savon_lessive", "Savon lessive", "soaps", ProductUnit.UNIT),
            ("detergent", "Détergent poudre", "cleaning", ProductUnit.PACK),
            ("papier", "Papier hygiénique", "hygiene", ProductUnit.PACK),
            ("eau", "Eau minérale", "drinks", ProductUnit.BOTTLE),
            ("jus", "Jus de fruits", "drinks", ProductUnit.BOTTLE),
            ("soda", "Soda", "drinks", ProductUnit.BOTTLE),
            ("couches", "Couches bébé", "baby", ProductUnit.PACK),
            ("lingettes", "Lingettes bébé", "baby", ProductUnit.PACK),
            ("farine", "Farine de blé", "food", ProductUnit.KG),
            ("haricot", "Haricot rouge", "food", ProductUnit.KG),
            ("mais", "Maïs", "rice", ProductUnit.KG),
            ("the", "Thé", "drinks", ProductUnit.PACK),
            ("cafe", "Café soluble", "drinks", ProductUnit.UNIT),
            ("vinaigre", "Vinaigre", "food", ProductUnit.BOTTLE),
            ("mayonnaise", "Mayonnaise", "food", ProductUnit.UNIT),
            ("pates", "Pâtes alimentaires", "rice", ProductUnit.PACK),
            ("serviette", "Serviettes hygiéniques", "hygiene", ProductUnit.PACK),
        ]
        products = {}

        for key, name, category_key, unit in specs:
            category = categories[category_key]
            product = Product.objects.filter(
                category=category,
                normalized_name=normalize_product_name(name),
            ).first()
            if product is None:
                product = create_product(
                    name=name,
                    category=category,
                    default_unit=unit,
                )
            products[key] = product

        return products

    def _seed_variants(
        self,
        products: dict[str, Product],
    ) -> dict[tuple[str, str], ProductVariant]:
        specs = [
            ("huile_mayor", "500 ml", {"volume_ml": 500}),
            ("huile_mayor", "1 L", {"volume_ml": 1000}),
            ("huile_mayor", "5 L", {"volume_ml": 5000}),
            ("eau", "1.5 L", {"volume_ml": 1500}),
            ("eau", "Pack 12", {"units": 12}),
            ("couches", "Taille M", {"size": "M"}),
            ("couches", "Taille L", {"size": "L"}),
        ]
        variants = {}

        for product_key, name, attributes in specs:
            product = products[product_key]
            variant = ProductVariant.objects.filter(
                product=product,
                normalized_name=normalize_variant_name(name),
            ).first()
            if variant is None:
                variant = create_product_variant(
                    product=product,
                    name=name,
                    attributes=attributes,
                )
            variants[(product_key, name)] = variant

        return variants

    def _seed_aliases(self, products: dict[str, Product]) -> None:
        specs = [
            ("huile_mayor", "Mayor"),
            ("huile_mayor", "huile mayor"),
            ("riz_parfume", "riz premium"),
            ("savon_azur", "Azur"),
            ("detergent", "omo"),
            ("eau", "eau"),
            ("couches", "pampers"),
        ]

        for product_key, alias in specs:
            product = products[product_key]
            normalized = normalize_alias(alias)
            if not ProductAlias.objects.filter(
                product=product,
                normalized_alias=normalized,
            ).exists():
                add_product_alias(product=product, alias=alias)

    def _seed_stores(self, users: dict[str, object]) -> dict[str, Store]:
        # Demo coordinates around Douala; they are illustrative, not real stores.
        specs = [
            ("bonamoussadi", "Marché Bonamoussadi", "674242450", 9.7357, 4.0907),
            ("akwa", "Épicerie Akwa", "696767542", 9.7043, 4.0511),
            ("bonapriso", "Boutique Démo Bonapriso", None, 9.7076, 4.0268),
            ("deido", "Boutique Démo Deido", None, 9.7126, 4.0702),
        ]
        stores = {}

        for key, name, phone, longitude, latitude in specs:
            store = Store.objects.filter(name=name).first()
            if store is None:
                store = create_store(
                    name=name,
                    phone=phone,
                    location=make_store_point(longitude, latitude),
                    city="Douala",
                    neighborhood=key.capitalize(),
                    service_radius_meters=5000,
                    created_by=users["owner_a"],
                )
            stores[key] = store

        add_store_member(
            stores["bonamoussadi"],
            users["manager_a"],
            StoreMembershipRole.MANAGER,
        )
        add_store_member(
            stores["bonamoussadi"],
            users["agent_a"],
            StoreMembershipRole.AGENT,
        )
        add_store_member(stores["akwa"], users["owner_b"], StoreMembershipRole.OWNER)

        return stores

    def _seed_drivers(self, users: dict[str, object]) -> None:
        specs = [
            ("driver_a", VehicleType.MOTORBIKE, DriverAvailability.AVAILABLE),
            ("driver_b", VehicleType.MOTORBIKE, DriverAvailability.BUSY),
            ("driver_c", VehicleType.CAR, DriverAvailability.OFFLINE),
            ("driver_d", VehicleType.VAN, DriverAvailability.AVAILABLE),
            ("driver_e", VehicleType.BICYCLE, DriverAvailability.OFFLINE),
        ]

        for user_key, vehicle_type, availability in specs:
            driver = DriverProfile.objects.filter(user=users[user_key]).first()
            if driver is None:
                driver = create_driver_profile(
                    users[user_key],
                    vehicle_type=vehicle_type,
                    service_radius_meters=5000,
                )
            activate_driver(driver)
            if availability == DriverAvailability.AVAILABLE:
                set_driver_available(driver)
            elif availability == DriverAvailability.BUSY:
                set_driver_busy(driver)

    def _seed_store_products(
        self,
        stores: dict[str, Store],
        products: dict[str, Product],
        variants: dict[tuple[str, str], ProductVariant],
    ) -> None:
        specs = [
            ("bonamoussadi", "riz", None, ProductAvailability.AVAILABLE, None),
            (
                "bonamoussadi",
                "huile_mayor",
                "1 L",
                ProductAvailability.AVAILABLE,
                "1800",
            ),
            ("bonamoussadi", "savon_azur", None, ProductAvailability.UNKNOWN, None),
            ("akwa", "riz", None, ProductAvailability.AVAILABLE, None),
            ("akwa", "sucre", None, ProductAvailability.AVAILABLE, "900"),
            ("akwa", "huile_mayor", None, ProductAvailability.UNAVAILABLE, None),
            ("bonapriso", "eau", "1.5 L", ProductAvailability.AVAILABLE, "500"),
            ("bonapriso", "jus", None, ProductAvailability.AVAILABLE, "700"),
            ("deido", "detergent", None, ProductAvailability.AVAILABLE, "1500"),
            ("deido", "papier", None, ProductAvailability.UNKNOWN, None),
        ]
        product_keys = list(products.keys())

        for index, key in enumerate(product_keys[:32]):
            store_key = list(stores.keys())[index % len(stores)]
            specs.append((store_key, key, None, ProductAvailability.AVAILABLE, None))

        for store_key, product_key, variant_name, availability, price in specs:
            store = stores[store_key]
            product = products[product_key]
            variant = (
                variants.get((product_key, variant_name)) if variant_name else None
            )
            store_product = StoreProduct.objects.filter(
                store=store,
                product=product,
                product_variant=variant,
            ).first()

            if store_product is None:
                store_product = add_product_to_store(
                    store=store,
                    product=product,
                    product_variant=variant,
                    availability_status=availability,
                )
            else:
                set_product_availability(store_product, availability)

            if price is not None:
                set_store_product_price(store_product, Decimal(price), "XAF")
