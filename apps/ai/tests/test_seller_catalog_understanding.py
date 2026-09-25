from decimal import Decimal

import pytest

from apps.ai.application.seller_catalog_understanding import (
    SellerCatalogIntent,
    SellerCatalogUnderstandingService,
)
from apps.ai.domain.exceptions import AIInvalidResponseError


class Result:
    def __init__(self, data):
        self.data = data


class AIStub:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error

    def generate_structured(self, request):
        assert request.task_name == "seller_catalog_command"
        if self.error:
            raise self.error
        return Result(self.data)


def payload(**changes):
    value = {
        "intent": "SELLER_CATALOG_ADD",
        "confidence": 0.9,
        "product_query": "Coca-Cola",
        "variant_query": "1.5 L",
        "price": 1000,
        "currency": "XAF",
        "availability": "AVAILABLE",
        "missing_fields": [],
    }
    value.update(changes)
    return value


def test_seller_catalog_contract_has_no_product_ids_and_parses_decimal():
    command = SellerCatalogUnderstandingService(AIStub(payload())).understand(
        "Ajoute Coca"
    )
    assert command.intent == SellerCatalogIntent.ADD
    assert command.price == Decimal("1000")
    assert not hasattr(command, "product_id")


def test_seller_catalog_contract_rejects_malformed_command():
    with pytest.raises(AIInvalidResponseError):
        SellerCatalogUnderstandingService(
            AIStub(payload(intent="NOT_A_COMMAND"))
        ).understand("x")
