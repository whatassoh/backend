"""Typed business-tool catalogue with centralized policy enforcement."""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any

from django.conf import settings
from jsonschema import Draft202012Validator, FormatChecker

from apps.ai.application.cache import EphemeralCache
from apps.ai.domain.agent_state import ToolRisk
from apps.catalog.application.selectors import search_products
from apps.conversations.application.interruptions import active_task_kind
from apps.conversations.domain.context import ConversationContext
from apps.drivers.application.selectors import get_driver_by_user
from apps.orders.application.selectors import get_customer_orders
from apps.stores.infrastructure.models import Store

logger = logging.getLogger(__name__)


class ToolPolicyError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    risk: ToolRisk
    permission: str
    confirmation_required: bool
    timeout_seconds: int
    retryable: bool
    redacted_fields: tuple[str, ...] = ()
    output_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "required": ["status", "data"],
            "properties": {
                "status": {"type": "string"},
                "data": {"type": "object"},
            },
            "additionalProperties": False,
        }
    )
    side_effects: str = "none"
    failure_modes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContext:
    user: Any
    conversation: Any
    shadow_mode: bool = True
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class ToolResult:
    status: str
    data: dict[str, Any]


class BusinessToolRegistry:
    """Models propose calls; this registry validates and authorizes execution."""

    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable] = {}

    def register(self, spec: ToolSpec, handler=None):
        if spec.name in self._specs:
            raise ValueError(f"Duplicate tool: {spec.name}")
        Draft202012Validator.check_schema(spec.input_schema)
        Draft202012Validator.check_schema(spec.output_schema)
        self._specs[spec.name] = spec
        if handler is not None:
            self._handlers[spec.name] = handler

    @property
    def specs(self):
        return MappingProxyType(self._specs)

    def manifest(self):
        cached = EphemeralCache.get("tool-manifest", "v2")
        if cached is not None:
            return cached
        manifest = [
            {
                **asdict(spec),
                "risk": spec.risk.value,
                "executable": spec.name in self._handlers,
            }
            for spec in sorted(self._specs.values(), key=lambda item: item.name)
        ]
        EphemeralCache.set(
            "tool-manifest",
            "v2",
            manifest,
            timeout=settings.AI_TOOL_MANIFEST_CACHE_SECONDS,
        )
        return manifest

    def execute(self, name, arguments, context: ToolContext):
        started = time.monotonic()
        spec = self._specs.get(name)
        if spec is None:
            raise ToolPolicyError("Tool is not allowlisted.")
        Draft202012Validator(
            spec.input_schema, format_checker=FormatChecker()
        ).validate(arguments)
        if context.shadow_mode and spec.risk != ToolRisk.R1:
            raise ToolPolicyError("Shadow mode forbids mutating tools.")
        if spec.confirmation_required and not context.confirmed:
            raise ToolPolicyError("Explicit confirmation is required.")
        self._authorize(spec, context)
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolPolicyError(
                "Tool is declared but not executable in this rollout."
            )
        try:
            result = handler(context, **arguments)
            serialized = asdict(result)
            Draft202012Validator(spec.output_schema).validate(serialized)
            size = len(json.dumps(serialized, default=str).encode())
            if size > settings.AI_TOOL_MAX_RESULT_BYTES:
                raise ToolPolicyError("Tool result exceeds the configured size limit.")
        except Exception as exc:
            logger.info(
                "ai_business_tool_failed",
                extra={
                    "tool_name": name,
                    "risk": spec.risk.value,
                    "error_category": exc.__class__.__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise
        logger.info(
            "ai_business_tool_completed",
            extra={
                "tool_name": name,
                "risk": spec.risk.value,
                "status": result.status,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return result

    @staticmethod
    def _authorize(spec, context):
        user_id = getattr(context.user, "id", None)
        conversation_user_id = getattr(context.conversation, "user_id", None)
        if user_id is None or conversation_user_id != user_id:
            raise ToolPolicyError(
                "Tool context is not authorized for this conversation."
            )
        if not spec.permission or spec.permission == "*":
            raise ToolPolicyError("Tool permission must be explicit.")


EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def build_business_tool_registry():
    registry = BusinessToolRegistry()
    registry.register(
        ToolSpec(
            "get_current_task",
            "1",
            "Read the active conversational task.",
            EMPTY_SCHEMA,
            ToolRisk.R1,
            "conversation.read",
            False,
            2,
            True,
        ),
        _get_current_task,
    )
    registry.register(
        ToolSpec(
            "search_products",
            "1",
            "Search active products by user wording.",
            {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 120}
                },
                "additionalProperties": False,
            },
            ToolRisk.R1,
            "catalog.read",
            False,
            3,
            True,
            ("query",),
        ),
        _search_products,
    )
    registry.register(
        ToolSpec(
            "list_orders",
            "1",
            "List orders owned by the authenticated customer.",
            EMPTY_SCHEMA,
            ToolRisk.R1,
            "orders.read_own",
            False,
            3,
            True,
        ),
        _list_orders,
    )
    registry.register(
        ToolSpec(
            "get_active_order",
            "1",
            "Read the active order referenced by trusted conversation state.",
            EMPTY_SCHEMA,
            ToolRisk.R1,
            "orders.read_own",
            False,
            3,
            True,
        ),
        _get_active_order,
    )
    registry.register(
        ToolSpec(
            "get_store_state",
            "1",
            "Read stores owned by the authenticated user.",
            EMPTY_SCHEMA,
            ToolRisk.R1,
            "stores.read_own",
            False,
            3,
            True,
        ),
        _get_store_state,
    )
    registry.register(
        ToolSpec(
            "get_driver_state",
            "1",
            "Read the authenticated driver's state.",
            EMPTY_SCHEMA,
            ToolRisk.R1,
            "drivers.read_own",
            False,
            3,
            True,
        ),
        _get_driver_state,
    )
    _register_extended_read_tools(registry)
    for name, description, risk, confirmation, permission in (
        (
            "suspend_task",
            "Pause the current task while preserving its resumable state.",
            ToolRisk.R2,
            False,
            "conversation.write",
        ),
        (
            "resume_task",
            "Restore the most recently suspended task after state validation.",
            ToolRisk.R2,
            False,
            "conversation.write",
        ),
        (
            "cancel_task",
            "Cancel only the active contextual task after explicit confirmation.",
            ToolRisk.R3,
            True,
            "conversation.write",
        ),
        (
            "add_item",
            "Add a grounded product and positive quantity to the customer's draft order.",
            ToolRisk.R2,
            False,
            "orders.write_own",
        ),
        (
            "remove_item",
            "Remove one unambiguously targeted item from the customer's draft order.",
            ToolRisk.R2,
            False,
            "orders.write_own",
        ),
        (
            "replace_item",
            "Replace one targeted draft item with a grounded catalog product.",
            ToolRisk.R2,
            False,
            "orders.write_own",
        ),
        (
            "set_location",
            "Set delivery coordinates supplied by a native channel location message.",
            ToolRisk.R2,
            False,
            "orders.write_own",
        ),
        (
            "review_order",
            "Build an authoritative review for the customer's active draft order.",
            ToolRisk.R1,
            False,
            "orders.read_own",
        ),
        (
            "confirm_order",
            "Confirm the reviewed customer order after explicit user confirmation.",
            ToolRisk.R3,
            True,
            "orders.confirm_own",
        ),
        (
            "cancel_order",
            "Cancel an eligible customer-owned order after explicit confirmation.",
            ToolRisk.R3,
            True,
            "orders.cancel_own",
        ),
        (
            "update_store_catalog",
            "Apply a confirmed price or availability change to an authorized store.",
            ToolRisk.R3,
            True,
            "stores.write_own",
        ),
        (
            "start_store_onboarding",
            "Start a resumable seller onboarding draft for this user.",
            ToolRisk.R2,
            False,
            "stores.write_own",
        ),
        (
            "set_store_location",
            "Set a seller onboarding location from native coordinates.",
            ToolRisk.R2,
            False,
            "stores.write_own",
        ),
        (
            "pause_store_onboarding",
            "Pause seller onboarding without discarding its validated draft.",
            ToolRisk.R2,
            False,
            "stores.write_own",
        ),
        (
            "set_driver_availability",
            "Change the authenticated active driver's availability state.",
            ToolRisk.R2,
            False,
            "drivers.write_own",
        ),
        (
            "report_delivery_issue",
            "Report a delivery issue on the authenticated driver's active mission.",
            ToolRisk.R3,
            True,
            "deliveries.write_own",
        ),
    ):
        registry.register(
            ToolSpec(
                name,
                "1",
                f"Declared for a future write rollout: {description}",
                {"type": "object"},
                risk,
                permission,
                confirmation,
                5,
                False,
                side_effects="disabled_not_executable",
                failure_modes=("NOT_ENABLED",),
            )
        )
    return registry


def _register_extended_read_tools(registry):
    read_tools = (
        (
            "get_suspended_tasks",
            "Read resumable conversation tasks without modifying their order.",
            EMPTY_SCHEMA,
            "conversation.read",
            _get_suspended_tasks,
        ),
        (
            "get_memory_preferences",
            "Read only this user's allowlisted communication preferences.",
            EMPTY_SCHEMA,
            "memory.read_own",
            _get_memory_preferences,
        ),
        (
            "get_response_style",
            "Read the resolved non-sensitive communication style for this conversation.",
            EMPTY_SCHEMA,
            "conversation.read",
            _get_response_style,
        ),
        (
            "list_active_order_items",
            "List items from the active order owned by the authenticated customer.",
            EMPTY_SCHEMA,
            "orders.read_own",
            _list_active_order_items,
        ),
        (
            "get_order_by_reference",
            "Read one order by reference only when it belongs to the authenticated customer.",
            _string_schema("reference", 80),
            "orders.read_own",
            _get_order_by_reference,
        ),
        (
            "list_store_catalog",
            "List declared prices and availability for a store managed by this user.",
            _uuid_schema("store_id"),
            "stores.read_own",
            _list_store_catalog,
        ),
        (
            "list_product_categories",
            "List active marketplace product categories.",
            EMPTY_SCHEMA,
            "catalog.read",
            _list_product_categories,
        ),
        (
            "list_marketplace_products",
            "List products currently declared available by active stores.",
            EMPTY_SCHEMA,
            "catalog.read",
            _list_marketplace_products,
        ),
        (
            "get_product_details",
            "Read an active product and its active variants after product search.",
            _uuid_schema("product_id"),
            "catalog.read",
            _get_product_details,
        ),
        (
            "get_active_delivery_mission",
            "Read the authenticated driver's active mission and allowed next actions.",
            EMPTY_SCHEMA,
            "deliveries.read_own",
            _get_active_delivery_mission,
        ),
        (
            "get_customer_delivery_tracking",
            "Read status and location freshness for a customer-owned order; exact GPS is redacted.",
            _string_schema("reference", 80),
            "deliveries.read_own",
            _get_customer_delivery_tracking,
        ),
        (
            "retrieve_knowledge",
            "Retrieve cited static help content after server-side ACL filtering.",
            _string_schema("query", 1200),
            "knowledge.read",
            _retrieve_knowledge,
        ),
    )
    for name, description, schema, permission, handler in read_tools:
        registry.register(
            ToolSpec(
                name=name,
                version="1",
                description=description,
                input_schema=schema,
                risk=ToolRisk.R1,
                permission=permission,
                confirmation_required=False,
                timeout_seconds=3,
                retryable=True,
                side_effects="read_only",
                failure_modes=("NOT_FOUND", "FORBIDDEN", "TEMPORARY_FAILURE"),
            ),
            handler,
        )


def _string_schema(name, maximum):
    return {
        "type": "object",
        "required": [name],
        "properties": {name: {"type": "string", "minLength": 1, "maxLength": maximum}},
        "additionalProperties": False,
    }


def _uuid_schema(name):
    schema = _string_schema(name, 36)
    schema["properties"][name]["format"] = "uuid"
    return schema


def _get_current_task(context):
    state = ConversationContext.from_payload(context.conversation.context)
    task = active_task_kind(state)
    return ToolResult(
        "OK", {"task": task.value if task else None, "flow": state.flow.value}
    )


def _search_products(context, query):
    products = search_products(query).only("id", "name")[:10]
    return ToolResult(
        "OK",
        {"products": [{"id": str(item.id), "name": item.name} for item in products]},
    )


def _list_orders(context):
    orders = get_customer_orders(context.user).only("id", "reference", "status")[:10]
    return ToolResult(
        "OK",
        {
            "orders": [
                {"id": str(item.id), "reference": item.reference, "status": item.status}
                for item in orders
            ]
        },
    )


def _get_active_order(context):
    state = ConversationContext.from_payload(context.conversation.context)
    if not state.active_order_id:
        return ToolResult("NOT_FOUND", {})
    order = (
        get_customer_orders(context.user)
        .only("id", "reference", "status")
        .filter(id=state.active_order_id)
        .first()
    )
    if order is None:
        return ToolResult("NOT_FOUND", {})
    return ToolResult(
        "OK",
        {"id": str(order.id), "reference": order.reference, "status": order.status},
    )


def _get_store_state(context):
    from apps.stores.domain.enums import StoreMembershipStatus

    stores = Store.objects.filter(
        memberships__user=context.user,
        memberships__status=StoreMembershipStatus.ACTIVE,
    ).only("id", "name", "status")[:10]
    return ToolResult(
        "OK",
        {
            "stores": [
                {"id": str(item.id), "name": item.name, "status": item.status}
                for item in stores
            ]
        },
    )


def _get_driver_state(context):
    driver = get_driver_by_user(context.user)
    return (
        ToolResult("NOT_FOUND", {})
        if driver is None
        else ToolResult(
            "OK",
            {
                "id": str(driver.id),
                "status": driver.status,
                "availability": driver.availability_status,
            },
        )
    )


def _get_suspended_tasks(context):
    state = ConversationContext.from_payload(context.conversation.context)
    return ToolResult(
        "OK",
        {
            "tasks": [
                {
                    "kind": task.kind.value,
                    "journey": task.journey.value if task.journey else None,
                    "flow": task.flow.value,
                    "reason": task.reason,
                }
                for task in state.suspended_tasks
            ]
        },
    )


def _get_memory_preferences(context):
    from apps.conversations.application.memory import ConversationMemoryService

    return ToolResult(
        "OK", {"preferences": ConversationMemoryService().safe_context(context.user)}
    )


def _get_response_style(context):
    from apps.conversations.application.style import style_snapshot

    return ToolResult("OK", {"style": style_snapshot(context.conversation)})


def _list_active_order_items(context):
    state = ConversationContext.from_payload(context.conversation.context)
    if not state.active_order_id:
        return ToolResult("NOT_FOUND", {})
    order = (
        get_customer_orders(context.user)
        .prefetch_related("items")
        .filter(pk=state.active_order_id)
        .first()
    )
    if order is None:
        return ToolResult("NOT_FOUND", {})
    return ToolResult(
        "OK",
        {
            "order_reference": order.reference,
            "status": order.status,
            "items": [
                {
                    "requested_name": item.requested_name,
                    "quantity": str(item.quantity),
                    "unit": item.unit,
                    "resolution_status": item.resolution_status,
                }
                for item in order.items.all()
            ],
        },
    )


def _get_order_by_reference(context, reference):
    order = (
        get_customer_orders(context.user)
        .prefetch_related("items")
        .filter(reference=reference)
        .first()
    )
    if order is None:
        return ToolResult("NOT_FOUND", {})
    return ToolResult(
        "OK",
        {
            "reference": order.reference,
            "status": order.status,
            "currency": order.currency,
            "item_count": order.items.count(),
            "has_delivery_location": order.delivery_location is not None,
        },
    )


def _list_store_catalog(context, store_id):
    from apps.catalog.application.selectors import get_store_catalog
    from apps.stores.domain.enums import StoreMembershipStatus

    store = Store.objects.filter(
        pk=store_id,
        memberships__user=context.user,
        memberships__status=StoreMembershipStatus.ACTIVE,
    ).first()
    if store is None:
        return ToolResult("NOT_FOUND", {})
    items = get_store_catalog(store)[:100]
    return ToolResult(
        "OK",
        {
            "store_id": str(store.id),
            "store_name": store.name,
            "items": [
                {
                    "product_id": str(item.product_id),
                    "name": item.display_name or item.product.name,
                    "variant": item.product_variant.name
                    if item.product_variant
                    else None,
                    "price": str(item.price) if item.price is not None else None,
                    "currency": item.currency,
                    "availability": item.availability_status,
                }
                for item in items
            ],
        },
    )


def _list_product_categories(context):
    from apps.catalog.application.selectors import get_active_categories

    categories = get_active_categories().only("id", "name", "parent_id")[:100]
    return ToolResult(
        "OK",
        {
            "categories": [
                {
                    "id": str(category.id),
                    "name": category.name,
                    "parent_id": str(category.parent_id)
                    if category.parent_id
                    else None,
                }
                for category in categories
            ]
        },
    )


def _list_marketplace_products(context):
    from apps.catalog.application.selectors import get_marketplace_available_products

    queryset = get_marketplace_available_products().order_by("name")
    total = queryset.count()
    return ToolResult(
        "OK" if total else "NOT_FOUND",
        {
            "count": total,
            "products": [
                {"id": str(product.id), "name": product.name}
                for product in queryset[:20]
            ],
        },
    )


def _get_product_details(context, product_id):
    from apps.catalog.application.selectors import get_product_by_id
    from apps.catalog.domain.enums import ProductStatus, ProductVariantStatus

    product = get_product_by_id(product_id)
    if product is None or product.status != ProductStatus.ACTIVE:
        return ToolResult("NOT_FOUND", {})
    return ToolResult(
        "OK",
        {
            "id": str(product.id),
            "name": product.name,
            "category": product.category.name,
            "default_unit": product.default_unit,
            "variants": [
                {"id": str(variant.id), "name": variant.name}
                for variant in product.variants.all()
                if variant.status == ProductVariantStatus.ACTIVE
            ],
        },
    )


def _get_active_delivery_mission(context):
    from apps.drivers.application.delivery_execution import (
        DeliveryExecutionService,
        DeliveryMissionNotFound,
    )

    try:
        mission = DeliveryExecutionService().get_active_mission(context.user)
    except DeliveryMissionNotFound:
        return ToolResult("NOT_FOUND", {})
    return ToolResult(
        "OK",
        {
            "assignment_id": mission.assignment_id,
            "order_reference": mission.order_reference,
            "order_status": mission.order_status,
            "pickup_name": mission.pickup.display_name,
            "destination_label": mission.destination.address_label,
            "allowed_actions": list(mission.allowed_actions),
        },
    )


def _get_customer_delivery_tracking(context, reference):
    from apps.drivers.application.tracking import DeliveryTrackingService
    from apps.drivers.application.delivery_execution import (
        DeliveryMissionAuthorizationError,
    )

    try:
        tracking = DeliveryTrackingService().get_customer_tracking(
            context.user, reference=reference
        )
    except DeliveryMissionAuthorizationError:
        return ToolResult("NOT_FOUND", {})
    return ToolResult(
        "OK",
        {
            "order_reference": tracking.order_reference,
            "order_status": tracking.order_status,
            "driver_assigned": tracking.driver.assigned,
            "driver_location_status": tracking.driver.location_status.value,
            "pickup_name": tracking.pickup.display_name if tracking.pickup else None,
            "destination_label": tracking.destination.address_label,
        },
    )


def _retrieve_knowledge(context, query):
    from apps.ai.application.runtime import get_ai_runtime_policy
    from apps.knowledge.application.access import principal_for_user
    from apps.knowledge.application.embeddings import get_embedding_provider
    from apps.knowledge.application.retrieval import HybridKnowledgeRetriever
    from apps.knowledge.application.security import (
        requires_authoritative_business_tool,
    )
    from apps.knowledge.domain.dtos import KnowledgeQuery

    if requires_authoritative_business_tool(query):
        return ToolResult(
            "NOT_ALLOWED",
            {"reason": "AUTHORITATIVE_BUSINESS_TOOL_REQUIRED", "chunks": []},
        )
    runtime = get_ai_runtime_policy()
    if not runtime.rag_enabled:
        return ToolResult("NOT_ALLOWED", {"reason": "RAG_DISABLED", "chunks": []})
    from apps.knowledge.application.rollout import rag_enabled_for

    if not rag_enabled_for(context.user.id):
        return ToolResult(
            "NOT_ALLOWED", {"reason": "RAG_ROLLOUT_EXCLUDED", "chunks": []}
        )
    embedding_provider = (
        get_embedding_provider() if runtime.rag_semantic_enabled else None
    )
    result = HybridKnowledgeRetriever(
        embedding_provider=embedding_provider
    ).retrieve(
        KnowledgeQuery(
            text=query,
            principal=principal_for_user(context.user),
            language=context.user.preferred_language,
            limit=min(settings.AI_RAG_MAX_RESULTS, 6),
        )
    )
    return ToolResult(
        "OK" if result.chunks else "NOT_FOUND",
        {
            "strategy": result.strategy.value,
            "chunks": [
                {
                    "chunk_id": str(chunk.chunk_id),
                    "source": chunk.source_title,
                    "version": chunk.source_version,
                    "url": chunk.canonical_url,
                    "content": chunk.content[:800],
                }
                for chunk in result.chunks
            ],
        },
    )
