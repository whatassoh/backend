from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point

from apps.ai.domain.understanding import (
    ExtractedItem,
    ExtractedUnit,
    InteractionMode,
    JourneyGoal,
    MessageIntent,
    OrderAction,
    UserMessageUnderstanding,
)
from apps.catalog.application.services import (
    add_product_alias,
    add_product_to_store,
    create_category,
    create_product,
    create_product_variant,
)
from apps.catalog.domain.enums import ProductAvailability, ProductUnit
from apps.conversations.application.orchestrator import ConversationalOrchestrator
from apps.conversations.application.product_discovery import parse_budget
from apps.conversations.application.services import get_or_create_active_conversation
from apps.conversations.domain.context import ConversationContext
from apps.drivers.infrastructure.models import DriverProfile
from apps.identity.infrastructure.models import User
from apps.messaging.application.services import create_channel_identity
from apps.messaging.domain.dtos import NormalizedInboundMessage
from apps.messaging.domain.enums import MessageType, MessagingChannel
from apps.orders.infrastructure.models import OrderItem
from apps.stores.application.services import create_store
from apps.telegram.adapters.inbound import parse_telegram_update
from apps.telegram.application.services import process_telegram_webhook
from apps.whatsapp.adapters.inbound import parse_whatsapp_webhook
from apps.whatsapp.application.services import process_whatsapp_webhook


class UnderstandingQueue:
    def __init__(self, *values):
        self.values = list(values)

    def understand_normalized(self, *_args, **_kwargs):
        return self.values.pop(0)


def understood_catalog(*items):
    return UserMessageUnderstanding(
        intent=MessageIntent.CATALOG_BROWSE,
        confidence=Decimal(".99"),
        items=tuple(items),
    )


def understood_buy(*items):
    return UserMessageUnderstanding(
        intent=MessageIntent.CREATE_OR_UPDATE_ORDER,
        confidence=Decimal(".99"),
        interaction_mode=InteractionMode.ACT,
        action_requested=True,
        journey_goal=JourneyGoal.BUY_PRODUCTS,
        order_action=OrderAction.ADD_ITEM,
        items=tuple(items),
    )


def conversation(channel=MessagingChannel.WHATSAPP, *, external_user_id=None):
    user = User.objects.create_user(whatsapp_phone=f"+2376{User.objects.count():08d}")
    identity = create_channel_identity(
        user=user,
        channel=channel,
        external_user_id=external_user_id or f"discovery-{user.id}",
    )
    return get_or_create_active_conversation(
        channel_identity=identity,
        external_chat_id=(
            external_user_id if channel == MessagingChannel.TELEGRAM else None
        ),
    )[0]


def inbound(text, *, channel=MessagingChannel.WHATSAPP, message_id="discovery-1"):
    return NormalizedInboundMessage(
        channel=channel,
        external_message_id=message_id,
        external_user_id="discovery-user",
        message_type=MessageType.TEXT,
        text=text,
    )


@pytest.fixture
def marketplace():
    store = create_store(
        name="Marketplace",
        location=Point(9.7, 4.05, srid=4326),
        city="Douala",
    )
    categories = {
        name: create_category(name=name)
        for name in ("Alimentation", "Boissons", "Bébé", "Nettoyage", "Téléphones")
    }
    return store, categories


def add_offer(
    marketplace,
    name,
    *,
    category="Alimentation",
    price=None,
    variant=None,
    unit=ProductUnit.UNIT,
):
    store, categories = marketplace
    product = create_product(
        name=name,
        category=categories[category],
        default_unit=unit,
    )
    product_variant = (
        create_product_variant(product=product, name=variant) if variant else None
    )
    add_product_to_store(
        store=store,
        product=product,
        product_variant=product_variant,
        price=price,
        availability_status=ProductAvailability.AVAILABLE,
    )
    return product, product_variant


@pytest.mark.django_db
def test_exact_available_product_is_targeted_not_global(marketplace):
    add_offer(marketplace, "Huile Mayor", price=Decimal("1800"))
    add_offer(marketplace, "Couches bébé", category="Bébé")
    convo = conversation()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="huile"))
        )
    )

    outcome = app.process_message(convo, inbound("Vous avez de l'huile ?"))

    assert "Huile Mayor" in outcome.response.text
    assert "Couches bébé" not in outcome.response.text
    assert outcome.metadata["authoritative"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("question", ["Je cherche le gaz", "Le gaz est disponible ?"])
def test_absent_product_is_explicit_and_never_prefixed_yes(marketplace, question):
    add_offer(marketplace, "Farine")
    convo = conversation()
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="gaz"))
        )
    ).process_message(convo, inbound(question))

    assert "pas trouvé de gaz" in outcome.response.text.casefold()
    assert not outcome.response.text.casefold().startswith("oui")
    assert "Farine" not in outcome.response.text


@pytest.mark.django_db
def test_multi_product_search_reports_each_truth_separately(marketplace):
    add_offer(marketplace, "Huile de palme")
    convo = conversation()
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(
                ExtractedItem(requested_name="huile"),
                ExtractedItem(requested_name="gaz"),
            )
        )
    ).process_message(convo, inbound("Vous avez de l'huile et du gaz ?"))

    assert "Huile de palme" in outcome.response.text
    assert "Gaz : aucun résultat disponible" in outcome.response.text
    assert outcome.metadata["multi_product"] is True


@pytest.mark.django_db
def test_full_catalog_total_is_real_and_followup_returns_all_twenty_others(marketplace):
    for index in range(28):
        add_offer(marketplace, f"Produit {index:02d}")
    convo = conversation()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(),
            understood_catalog(),
        )
    )

    first = app.process_message(
        convo, inbound("Quels produits avez-vous ?", message_id="catalog-page-1")
    )
    second = app.process_message(
        convo,
        inbound("Les 20 autres produits c'est quoi ?", message_id="catalog-page-2"),
    )

    assert "28 produit(s)" in first.response.text
    assert "20 autre(s)" in first.response.text
    assert "Produit 00" in first.response.text
    assert "Produit 08" not in first.response.text
    assert "Produit 08" in second.response.text
    assert "Produit 27" in second.response.text
    assert "déjà terminée" not in second.response.text


@pytest.mark.django_db
@pytest.mark.parametrize(
    "wording",
    [
        "J'ai 500k",
        "maximum 500 000",
        "pas plus de 500 mille",
        "moins de 500000",
    ],
)
def test_natural_budget_returns_only_compatible_phones(marketplace, wording):
    add_offer(
        marketplace,
        "Téléphone Accessible",
        category="Téléphones",
        price=Decimal("450000"),
    )
    add_offer(
        marketplace,
        "Téléphone Premium",
        category="Téléphones",
        price=Decimal("700000"),
    )
    convo = conversation()
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="téléphone"))
        )
    ).process_message(convo, inbound(f"Je veux un téléphone, {wording}"))

    assert "Téléphone Accessible" in outcome.response.text
    assert "Téléphone Premium" not in outcome.response.text
    assert "500 000 FCFA" in outcome.response.text


@pytest.mark.django_db
def test_no_phone_does_not_return_unrelated_products(marketplace):
    add_offer(marketplace, "Couches bébé", category="Bébé", price=Decimal("8000"))
    convo = conversation()
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="téléphone"))
        )
    ).process_message(convo, inbound("Quel téléphone avec 500k ?"))

    assert "aucun téléphone" in outcome.response.text.casefold()
    assert "Couches" not in outcome.response.text


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("wording", "concept", "expected"),
    [
        ("Je veux quelque chose à manger", "Alimentation", "Riz parfumé"),
        ("Je cherche un truc à boire", "Boissons", "Jus d'ananas"),
        ("Vous avez des produits pour bébé ?", "Bébé", "Couches bébé"),
        ("Quelque chose pour nettoyer", "Nettoyage", "Savon vaisselle"),
    ],
)
def test_category_and_need_search_use_real_categories(
    marketplace, wording, concept, expected
):
    add_offer(marketplace, "Riz parfumé", category="Alimentation")
    add_offer(marketplace, "Jus d'ananas", category="Boissons")
    add_offer(marketplace, "Couches bébé", category="Bébé")
    add_offer(marketplace, "Savon vaisselle", category="Nettoyage")
    convo = conversation()
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name=concept))
        )
    ).process_message(convo, inbound(wording))

    assert expected in outcome.response.text


@pytest.mark.django_db
def test_alias_and_variant_results_are_real(marketplace):
    store, categories = marketplace
    product = create_product(
        name="Huile Mayor",
        category=categories["Alimentation"],
        default_unit=ProductUnit.BOTTLE,
    )
    add_product_alias(product=product, alias="huille mayor")
    variant = create_product_variant(product=product, name="1 L")
    add_product_to_store(
        store=store,
        product=product,
        product_variant=variant,
        price=Decimal("1800"),
        availability_status=ProductAvailability.AVAILABLE,
    )
    convo = conversation()
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(
                ExtractedItem(requested_name="huille mayor", variant_hint="1L")
            )
        )
    ).process_message(convo, inbound("Vous avez de l'huille Mayor 1L ?"))

    assert "Huile Mayor — 1 L" in outcome.response.text
    assert "1 800 FCFA" in outcome.response.text


@pytest.mark.django_db
def test_absent_variant_proposes_only_available_real_formats(marketplace):
    store, categories = marketplace
    product = create_product(name="Huile Mayor", category=categories["Alimentation"])
    for name in ("1 L", "5 L"):
        variant = create_product_variant(product=product, name=name)
        add_product_to_store(
            store=store,
            product=product,
            product_variant=variant,
            availability_status=ProductAvailability.AVAILABLE,
        )
    convo = conversation()
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(
                ExtractedItem(requested_name="Huile Mayor", variant_hint="20 L")
            )
        )
    ).process_message(convo, inbound("Mayor 20 L ?"))

    assert "pas trouvé le format 20 L" in outcome.response.text
    assert "1 L" in outcome.response.text
    assert "5 L" in outcome.response.text


@pytest.mark.django_db
def test_discovery_followup_hands_real_product_and_variant_to_existing_buy_flow(
    marketplace,
):
    store, categories = marketplace
    product = create_product(
        name="Huile Mayor",
        category=categories["Alimentation"],
        default_unit=ProductUnit.BOTTLE,
    )
    variant = create_product_variant(product=product, name="1 L")
    add_product_to_store(
        store=store,
        product=product,
        product_variant=variant,
        price=Decimal("1800"),
        availability_status=ProductAvailability.AVAILABLE,
    )
    convo = conversation()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="Huile Mayor")),
            understood_buy(
                ExtractedItem(
                    requested_name="celle-ci",
                    quantity=Decimal("2"),
                    unit=ExtractedUnit.BOTTLE,
                    variant_hint="1 L",
                )
            ),
        )
    )

    app.process_message(convo, inbound("Vous avez Mayor ?", message_id="discover"))
    app.process_message(convo, inbound("Je veux 2 de 1L", message_id="buy-discovered"))

    item = OrderItem.objects.get()
    assert item.product_id == product.id
    assert item.product_variant_id == variant.id
    assert item.quantity == Decimal("2")


@pytest.mark.django_db
def test_no_result_is_understood_not_a_retry(marketplace):
    convo = conversation()
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="PlayStation 12"))
        )
    ).process_message(convo, inbound("Je cherche une PlayStation 12"))

    assert outcome.metadata["no_results"] is True
    assert "reformuler" not in outcome.response.text
    assert "PlayStation 12" in outcome.response.text


@pytest.mark.django_db
def test_ordinal_followup_resolves_server_stored_product_id(marketplace):
    first, _ = add_offer(marketplace, "Boisson A", category="Boissons")
    second, _ = add_offer(marketplace, "Boisson B", category="Boissons")
    convo = conversation()
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="Boissons")),
            understood_catalog(),
        )
    )

    app.process_message(convo, inbound("Que proposes-tu à boire ?", message_id="list"))
    selected = app.process_message(convo, inbound("La deuxième", message_id="ordinal"))

    assert str(second.id) == selected.metadata["selected_product_id"]
    assert str(first.id) != selected.metadata["selected_product_id"]


@pytest.mark.django_db
def test_multi_role_user_can_discover_without_seller_or_driver_capture(marketplace):
    product, _ = add_offer(marketplace, "Riz parfumé")
    convo = conversation()
    create_store(
        name="Sa boutique",
        location=Point(9.71, 4.06, srid=4326),
        city="Douala",
        created_by=convo.user,
    )
    DriverProfile.objects.create(user=convo.user)
    outcome = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="riz"))
        )
    ).process_message(convo, inbound("Vous avez du riz ?"))

    assert str(product.id) in {
        item["product_id"]
        for item in ConversationContext.from_payload(
            convo.__class__.objects.get(pk=convo.pk).context
        ).metadata["product_discovery"]["visible"]
    }
    assert outcome.metadata["capability"] == "product_discovery"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("j'ai 500k", (None, Decimal("500000"))),
        ("moins de 500 000", (None, Decimal("500000"))),
        ("pas plus de 500 mille", (None, Decimal("500000"))),
        ("entre 10000 et 20000", (Decimal("10000"), Decimal("20000"))),
        ("autour de 5000", (None, Decimal("5000"))),
    ],
)
def test_budget_parser_supports_natural_xaf_forms(text, expected):
    assert parse_budget(text) == expected


@pytest.mark.django_db
def test_whatsapp_payload_reaches_discovery_response_plan_and_outbound(
    marketplace, monkeypatch
):
    add_offer(marketplace, "Riz WhatsApp")
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "237699000001",
                                    "id": "wamid.discovery",
                                    "type": "text",
                                    "text": {"body": "Vous avez du riz ?"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    normalized = parse_whatsapp_webhook(payload)[0]
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="riz"))
        )
    )
    sent = []
    monkeypatch.setattr(
        "apps.whatsapp.application.services.process_inbound_conversation",
        lambda convo, message, **kwargs: app.process_message(convo, message, **kwargs),
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )
    process_whatsapp_webhook(payload)

    assert normalized.channel == MessagingChannel.WHATSAPP
    assert "Riz WhatsApp" in sent[0]["response"].text
    assert sent[0]["response"].metadata["authoritative"] is True


@pytest.mark.django_db
def test_telegram_payload_reaches_discovery_response_plan_and_outbound(
    marketplace, monkeypatch
):
    add_offer(marketplace, "Riz Telegram")
    payload = {
        "update_id": 42,
        "message": {
            "message_id": 7,
            "date": 1700000000,
            "from": {"id": 1234, "first_name": "Awa"},
            "chat": {"id": 5678, "type": "private"},
            "text": "Vous avez du riz ?",
        },
    }
    normalized = parse_telegram_update(payload)[0]
    app = ConversationalOrchestrator(
        understanding_service=UnderstandingQueue(
            understood_catalog(ExtractedItem(requested_name="riz"))
        )
    )
    sent = []
    monkeypatch.setattr(
        "apps.telegram.application.services.process_inbound_conversation",
        lambda convo, message, **kwargs: app.process_message(convo, message, **kwargs),
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )
    process_telegram_webhook(payload)

    assert normalized.channel == MessagingChannel.TELEGRAM
    assert "Riz Telegram" in sent[0]["response"].text
    assert sent[0]["response"].metadata["authoritative"] is True
