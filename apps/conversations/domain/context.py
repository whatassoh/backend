from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ConversationFlow(StrEnum):
    IDLE = "IDLE"
    ORDER_BUILDING = "ORDER_BUILDING"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    AWAITING_LOCATION = "AWAITING_LOCATION"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    AWAITING_JOURNEY_SELECTION = "AWAITING_JOURNEY_SELECTION"
    SELLER_ONBOARDING = "SELLER_ONBOARDING"
    DRIVER_ONBOARDING = "DRIVER_ONBOARDING"


class ActiveJourney(StrEnum):
    """A temporary route for one conversation, never an identity role."""

    BUY = "BUY"
    SELL = "SELL"
    DELIVER = "DELIVER"


class SellerOnboardingStage(StrEnum):
    NAME = "NAME"
    LOCATION = "LOCATION"
    CITY = "CITY"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"


class DriverOnboardingStage(StrEnum):
    VEHICLE_TYPE = "VEHICLE_TYPE"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"


class SellerCatalogAction(StrEnum):
    LIST = "LIST"
    ADD = "ADD"
    UPDATE_PRICE = "UPDATE_PRICE"
    SET_AVAILABILITY = "SET_AVAILABILITY"
    REMOVE = "REMOVE"


class ClarificationKind(StrEnum):
    MISSING_QUANTITY = "MISSING_QUANTITY"
    MISSING_UNIT = "MISSING_UNIT"
    AMBIGUOUS_PRODUCT = "AMBIGUOUS_PRODUCT"
    MISSING_LOCATION = "MISSING_LOCATION"
    CONFIRMATION = "CONFIRMATION"
    OTHER = "OTHER"


class ClarificationStatus(StrEnum):
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY = "READY"
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    INTERRUPTED = "INTERRUPTED"


class ResponseInstruction(StrEnum):
    ASK_QUANTITY = "ASK_QUANTITY"
    ASK_UNIT = "ASK_UNIT"
    ASK_PRODUCT_CHOICE = "ASK_PRODUCT_CHOICE"
    REQUEST_LOCATION = "REQUEST_LOCATION"
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"
    NONE = "NONE"


@dataclass(frozen=True, kw_only=True)
class PendingClarification:
    kind: ClarificationKind
    field: str
    item_reference: str | None = None
    candidate_ids: tuple[str, ...] = ()
    created_at: str | None = None
    attempts: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class SellerOnboardingDraft:
    stage: SellerOnboardingStage
    store_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    city: str | None = None
    store_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class DriverOnboardingDraft:
    stage: DriverOnboardingStage
    vehicle_type: str | None = None
    driver_profile_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class PendingSellerCatalogMutation:
    action: SellerCatalogAction
    store_id: str
    product_id: str | None = None
    variant_id: str | None = None
    store_product_id: str | None = None
    price: str | None = None
    currency: str | None = None
    availability: str | None = None
    expected_updated_at: str | None = None
    candidates: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, kw_only=True)
class ConversationContext:
    flow: ConversationFlow = ConversationFlow.IDLE
    active_order_id: str | None = None
    pending_clarification: PendingClarification | None = None
    last_understanding: dict[str, Any] | None = None
    processed_message_ids: tuple[str, ...] = ()
    active_journey: ActiveJourney | None = None
    seller_onboarding: SellerOnboardingDraft | None = None
    catalog_setup_choice: str | None = None
    driver_onboarding: DriverOnboardingDraft | None = None
    pending_seller_catalog_mutation: PendingSellerCatalogMutation | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["flow"] = self.flow.value
        if self.pending_clarification:
            payload["pending_clarification"]["kind"] = (
                self.pending_clarification.kind.value
            )
            payload["pending_clarification"]["candidate_ids"] = list(
                self.pending_clarification.candidate_ids
            )
        payload["processed_message_ids"] = list(self.processed_message_ids)
        payload["active_journey"] = (
            self.active_journey.value if self.active_journey else None
        )
        if self.seller_onboarding:
            payload["seller_onboarding"]["stage"] = self.seller_onboarding.stage.value
        if self.driver_onboarding:
            payload["driver_onboarding"]["stage"] = self.driver_onboarding.stage.value
        if self.pending_seller_catalog_mutation:
            payload["pending_seller_catalog_mutation"]["action"] = (
                self.pending_seller_catalog_mutation.action.value
            )
            payload["pending_seller_catalog_mutation"]["candidates"] = list(
                self.pending_seller_catalog_mutation.candidates
            )
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ConversationContext":
        payload = payload or {}
        pending_data = payload.get("pending_clarification")
        onboarding_data = payload.get("seller_onboarding")
        driver_onboarding_data = payload.get("driver_onboarding")
        catalog_data = payload.get("pending_seller_catalog_mutation")
        pending = None
        if pending_data:
            pending = PendingClarification(
                kind=ClarificationKind(pending_data["kind"]),
                field=pending_data["field"],
                item_reference=pending_data.get("item_reference"),
                candidate_ids=tuple(pending_data.get("candidate_ids", ())),
                created_at=pending_data.get("created_at"),
                attempts=int(pending_data.get("attempts", 0)),
                metadata=dict(pending_data.get("metadata", {})),
            )
        onboarding = None
        if onboarding_data:
            onboarding = SellerOnboardingDraft(
                stage=SellerOnboardingStage(onboarding_data["stage"]),
                store_name=onboarding_data.get("store_name"),
                latitude=onboarding_data.get("latitude"),
                longitude=onboarding_data.get("longitude"),
                city=onboarding_data.get("city"),
                store_id=onboarding_data.get("store_id"),
            )
        driver_onboarding = None
        if driver_onboarding_data:
            driver_onboarding = DriverOnboardingDraft(
                stage=DriverOnboardingStage(driver_onboarding_data["stage"]),
                vehicle_type=driver_onboarding_data.get("vehicle_type"),
                driver_profile_id=driver_onboarding_data.get("driver_profile_id"),
            )
        pending_catalog = None
        if catalog_data:
            pending_catalog = PendingSellerCatalogMutation(
                action=SellerCatalogAction(catalog_data["action"]),
                store_id=catalog_data["store_id"],
                product_id=catalog_data.get("product_id"),
                variant_id=catalog_data.get("variant_id"),
                store_product_id=catalog_data.get("store_product_id"),
                price=catalog_data.get("price"),
                currency=catalog_data.get("currency"),
                availability=catalog_data.get("availability"),
                expected_updated_at=catalog_data.get("expected_updated_at"),
                candidates=tuple(catalog_data.get("candidates", ())),
            )
        return cls(
            flow=ConversationFlow(payload.get("flow", ConversationFlow.IDLE)),
            active_order_id=payload.get("active_order_id"),
            pending_clarification=pending,
            last_understanding=payload.get("last_understanding"),
            processed_message_ids=tuple(payload.get("processed_message_ids", ())),
            active_journey=(
                ActiveJourney(payload["active_journey"])
                if payload.get("active_journey")
                else None
            ),
            seller_onboarding=onboarding,
            catalog_setup_choice=payload.get("catalog_setup_choice"),
            driver_onboarding=driver_onboarding,
            pending_seller_catalog_mutation=pending_catalog,
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(frozen=True, kw_only=True)
class ClarificationResult:
    status: ClarificationStatus
    resolved_values: dict[str, Any] = field(default_factory=dict)
    next_flow: ConversationFlow = ConversationFlow.IDLE
    pending_clarification: PendingClarification | None = None
    response_instruction: ResponseInstruction = ResponseInstruction.NONE
    metadata: dict[str, Any] = field(default_factory=dict)
