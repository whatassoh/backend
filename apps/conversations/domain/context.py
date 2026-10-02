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


class ConversationTaskKind(StrEnum):
    """Resumable conversational work; never a substitute for business state."""

    ORDER = "ORDER"
    SELLER_ONBOARDING = "SELLER_ONBOARDING"
    SELLER_CATALOG = "SELLER_CATALOG"
    DRIVER_ONBOARDING = "DRIVER_ONBOARDING"
    DELIVERY_INSTRUCTIONS = "DELIVERY_INSTRUCTIONS"
    JOURNEY = "JOURNEY"


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
class PendingJourneySwitch:
    """An explicit, unconfirmed request to replace the foreground journey."""

    source: ActiveJourney
    target: ActiveJourney
    requested_at: str | None = None
    resume_task_kind: ConversationTaskKind | None = None


@dataclass(frozen=True, kw_only=True)
class SuspendedConversationTask:
    """Minimal typed snapshot needed to restore one foreground task exactly."""

    kind: ConversationTaskKind
    journey: ActiveJourney | None
    flow: ConversationFlow
    suspended_at: str
    reason: str
    active_order_id: str | None = None
    pending_clarification: PendingClarification | None = None
    seller_onboarding: SellerOnboardingDraft | None = None
    catalog_setup_choice: str | None = None
    driver_onboarding: DriverOnboardingDraft | None = None
    pending_seller_catalog_mutation: PendingSellerCatalogMutation | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # ``0`` denotes the compact pre-Lot-5 payload.  New snapshots are marked
    # explicitly by the suspension helper, so old payloads continue to resume
    # with their historic, non-destructive semantics.
    snapshot_version: int = 0


@dataclass(frozen=True, kw_only=True)
class ConversationSocialState:
    """Minimal social memory used to prevent repetitive scripted replies."""

    greeting_count: int = 0
    last_greeting_at: str | None = None
    introduced_at: str | None = None


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
    pending_journey_switch: PendingJourneySwitch | None = None
    suspended_tasks: tuple[SuspendedConversationTask, ...] = ()
    social_state: ConversationSocialState = field(
        default_factory=ConversationSocialState
    )
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
        if self.pending_journey_switch:
            payload["pending_journey_switch"]["source"] = (
                self.pending_journey_switch.source.value
            )
            payload["pending_journey_switch"]["target"] = (
                self.pending_journey_switch.target.value
            )
            payload["pending_journey_switch"]["resume_task_kind"] = (
                self.pending_journey_switch.resume_task_kind.value
                if self.pending_journey_switch.resume_task_kind
                else None
            )
        for task in payload["suspended_tasks"]:
            task["kind"] = task["kind"].value
            task["journey"] = task["journey"].value if task["journey"] else None
            task["flow"] = task["flow"].value
            _serialize_task_snapshot(task)
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ConversationContext":
        payload = payload or {}
        pending_data = payload.get("pending_clarification")
        onboarding_data = payload.get("seller_onboarding")
        driver_onboarding_data = payload.get("driver_onboarding")
        catalog_data = payload.get("pending_seller_catalog_mutation")
        switch_data = payload.get("pending_journey_switch")
        suspended_data = payload.get("suspended_tasks", ())
        social_data = payload.get("social_state") or {}
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
        pending_switch = None
        if switch_data:
            pending_switch = PendingJourneySwitch(
                source=ActiveJourney(switch_data["source"]),
                target=ActiveJourney(switch_data["target"]),
                requested_at=switch_data.get("requested_at"),
                resume_task_kind=(
                    ConversationTaskKind(switch_data["resume_task_kind"])
                    if switch_data.get("resume_task_kind")
                    else None
                ),
            )
        suspended_tasks = tuple(
            SuspendedConversationTask(
                kind=ConversationTaskKind(task["kind"]),
                journey=(
                    ActiveJourney(task["journey"]) if task.get("journey") else None
                ),
                flow=ConversationFlow(task.get("flow", ConversationFlow.IDLE)),
                suspended_at=task["suspended_at"],
                reason=task.get("reason", "user_pause"),
                active_order_id=task.get("active_order_id"),
                pending_clarification=_pending_from_payload(
                    task.get("pending_clarification")
                ),
                seller_onboarding=_seller_onboarding_from_payload(
                    task.get("seller_onboarding")
                ),
                catalog_setup_choice=task.get("catalog_setup_choice"),
                driver_onboarding=_driver_onboarding_from_payload(
                    task.get("driver_onboarding")
                ),
                pending_seller_catalog_mutation=_catalog_mutation_from_payload(
                    task.get("pending_seller_catalog_mutation")
                ),
                metadata=dict(task.get("metadata", {})),
                snapshot_version=int(task.get("snapshot_version", 0)),
            )
            for task in suspended_data
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
            pending_journey_switch=pending_switch,
            suspended_tasks=suspended_tasks,
            social_state=ConversationSocialState(
                greeting_count=max(0, int(social_data.get("greeting_count", 0))),
                last_greeting_at=social_data.get("last_greeting_at"),
                introduced_at=social_data.get("introduced_at"),
            ),
            metadata=dict(payload.get("metadata", {})),
        )


def _serialize_task_snapshot(task: dict[str, Any]) -> None:
    pending = task.get("pending_clarification")
    if pending:
        pending["kind"] = pending["kind"].value
        pending["candidate_ids"] = list(pending.get("candidate_ids", ()))
    onboarding = task.get("seller_onboarding")
    if onboarding:
        onboarding["stage"] = onboarding["stage"].value
    driver_onboarding = task.get("driver_onboarding")
    if driver_onboarding:
        driver_onboarding["stage"] = driver_onboarding["stage"].value
    catalog = task.get("pending_seller_catalog_mutation")
    if catalog:
        catalog["action"] = catalog["action"].value
        catalog["candidates"] = list(catalog.get("candidates", ()))


def _pending_from_payload(payload: dict[str, Any] | None):
    if not payload:
        return None
    return PendingClarification(
        kind=ClarificationKind(payload["kind"]),
        field=payload["field"],
        item_reference=payload.get("item_reference"),
        candidate_ids=tuple(payload.get("candidate_ids", ())),
        created_at=payload.get("created_at"),
        attempts=int(payload.get("attempts", 0)),
        metadata=dict(payload.get("metadata", {})),
    )


def _seller_onboarding_from_payload(payload: dict[str, Any] | None):
    if not payload:
        return None
    return SellerOnboardingDraft(
        stage=SellerOnboardingStage(payload["stage"]),
        store_name=payload.get("store_name"),
        latitude=payload.get("latitude"),
        longitude=payload.get("longitude"),
        city=payload.get("city"),
        store_id=payload.get("store_id"),
    )


def _driver_onboarding_from_payload(payload: dict[str, Any] | None):
    if not payload:
        return None
    return DriverOnboardingDraft(
        stage=DriverOnboardingStage(payload["stage"]),
        vehicle_type=payload.get("vehicle_type"),
        driver_profile_id=payload.get("driver_profile_id"),
    )


def _catalog_mutation_from_payload(payload: dict[str, Any] | None):
    if not payload:
        return None
    return PendingSellerCatalogMutation(
        action=SellerCatalogAction(payload["action"]),
        store_id=payload["store_id"],
        product_id=payload.get("product_id"),
        variant_id=payload.get("variant_id"),
        store_product_id=payload.get("store_product_id"),
        price=payload.get("price"),
        currency=payload.get("currency"),
        availability=payload.get("availability"),
        expected_updated_at=payload.get("expected_updated_at"),
        candidates=tuple(payload.get("candidates", ())),
    )


@dataclass(frozen=True, kw_only=True)
class ClarificationResult:
    status: ClarificationStatus
    resolved_values: dict[str, Any] = field(default_factory=dict)
    next_flow: ConversationFlow = ConversationFlow.IDLE
    pending_clarification: PendingClarification | None = None
    response_instruction: ResponseInstruction = ResponseInstruction.NONE
    metadata: dict[str, Any] = field(default_factory=dict)
