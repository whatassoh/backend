# Conversational Orchestration Audit

> Scope: static code and existing-test audit, 23 September 2026. No application code, migration, prompt, state machine, or test was changed. “Proved” means directly established by code; “Tested” means covered by a repository test; runtime/provider outcomes are explicitly marked where they cannot be established statically.

## 1. Executive Summary

The backend already keeps the LLM outside ORM mutation: `MessageUnderstandingService` returns typed facts and `ConversationalOrchestrator` invokes deterministic services. Structured actions bypass the LLM. This is the right foundation.

The principal mismatch with the requested MVP is scope. `ActiveCustomerOrderService` locks and validates **one Conversation**, then creates/reuses the ID stored in that conversation JSON. It never queries another draft for the same `User`. Two channel conversations can therefore create two `DRAFT`s. No database constraint closes that race.

Routing is intentionally stateful: onboarding, catalog conversation, location and delivery-instructions handlers short-circuit *before* understanding. Thus a pending workflow can consume an unrelated message. Natural cancellation works if the AI emits `CANCEL`, but order history/status/resume are not intents. Replacement supports only the regex `remplace X par Y`; the observed “À la place…” wording cannot reach it, and plural/singular matching is exact case-folded equality.

## 2. Current Inbound Pipeline

**Proved:**

```text
WhatsAppWebhookView / TelegramWebhookView
  -> process_*_webhook (`apps/*/application/services.py`)
  -> parse_* inbound adapter -> NormalizedInboundMessage
  -> MessageEvent (provider idempotency) + ingest_inbound_message
  -> ChannelIdentity/User + Conversation + ConversationMessage
  -> process_inbound_conversation (`conversations/application/flow.py`)
  -> ConversationalOrchestrator.process_message/_process
  -> ResponsePlan/ConversationalOutcome
  -> deliver_outcome_response -> channel provider -> outbound MessageEvent/ConversationMessage
```

`process_inbound_conversation` only enables `MessageUnderstandingService` when `AI_ENABLED`; otherwise its understanding service returns `None`. `ConversationMessage` persists inbound text/location/metadata. The webhook records raw provider payload in `MessageEvent`; this is operationally useful but is PII-sensitive.

## 3. Current Routing Precedence

**Proved, exact `_process` order:** seller onboarding; driver onboarding; seller catalog conversation (when configured); native location (driver location only for `DELIVER`); `awaiting_delivery_instructions`; structured `action_id` (driver offer, delivery, journey selection, availability, generic action); natural driver availability; AI understanding; confidence threshold; `SELL`/`DELIVER`/buy journey selection; `GREETING`; `HELP`/`UNKNOWN`; `CANCEL`; `CONFIRM`; text address; `MODIFY`; item handling.

Consequently an onboarding/catalog/location/instructions context intercepts before intent classification. Structured actions correctly bypass AI. A pending generic clarification itself is not an early return, so `CANCEL` and `HELP` can escape it (also tested by `test_cancel_and_help_do_not_force_old_pending_context`).

## 4. Conversation Context Model

`Conversation.context` is typed by `ConversationContext` and write-locked by `ConversationContextService`; it is per conversation, persisted JSON, and cleared on order confirmation/closed conversation.

| Field | Writers/readers and lifecycle | Lock/staleness/reconstructibility |
|---|---|---|
| `flow` | orchestrator, clarification, journey/onboarding; routing/recovery | Can become stale; derivable partly from order + pending state |
| `active_journey` | journey router; read by location/driver/catalog routing | Persists across topic changes; not business truth |
| `active_order_id` | active-order service; validated/used by mutations/recovery | Pointer, not authoritative: ownership/status is rechecked. Still stale and conversation-scoped |
| `pending_clarification` | clarification engine; cleared after mutations | Blocks readiness/recovery; no expiry; only partly reconstructible |
| `last_understanding` | declared but no production writer/reader found | Dead/stale-capable field |
| `processed_message_ids` | post-success orchestrator idempotency, capped at 20 | Reconstructible only from message records; bounded replay window |
| onboarding/catalog drafts | their services; short-circuit routing | Persisted JSON, potentially stale; domain records are partially reconstructible |
| `metadata.awaiting_delivery_instructions` | delivery code; read before AI | A hard route lock; arbitrary JSON, no typed expiry |

## 5. Active Journey Behavior

`JourneyRoutingService.select` changes only temporary `active_journey`; it deliberately preserves order/clarification. `BUY` is selected for `CREATE_OR_UPDATE_ORDER`; `SELL` and `DELIVER` select onboarding/readiness. This permits role switching but does not provide a suspended-workflow stack or return target.

## 6. Current Intent Model

**Exact AI enum:** `CREATE_OR_UPDATE_ORDER`, `PROVIDE_LOCATION`, `CONFIRM`, `MODIFY`, `CANCEL`, `GREETING`, `HELP`, `UNKNOWN`, `SELL`, `DELIVER`. Schema: intent, confidence, items (`requested_name`, quantity, unit, notes), location text, confirmation, missing fields, ambiguities, language.

There are no `ORDER_HISTORY`, `ORDER_STATUS`, `ORDER_RESUME`, separate add/remove/replace, or out-of-scope intents. Remove/replace are imperative text patterns inside the `MODIFY` handler. Product identity is grounded deterministically; no LLM product/order/item UUID is accepted.

## 7. Greeting Behavior

**Proved:** `GREETING` first calls recovery for persisted conversations. If there is an active flow, it resumes it; with no journey it returns “Bonjour ! Que souhaitez-vous faire ?” plus Buy/Sell/Deliver buttons; with a journey it returns a short greeting. **Tested:** greeting does not create an order or call catalog (`test_greeting_does_not_create_order_or_call_catalog`), and stable actions are tested in journey routing.

Whether “Bonjour, comment allez-vous ?” is classified as `GREETING` depends on the configured provider/prompt: **runtime validation required**.

## 8. Out-of-Scope Behavior

There is no explicit out-of-scope intent or response. If the model emits `UNKNOWN` and no active journey, router shows the main journey menu; with a journey it returns generic reformulation text. Therefore “1+1” can produce the menu only if classified `UNKNOWN`/low confidence with no journey. Provider classification is **not statically provable**.

## 9. Unknown / Low-Confidence Behavior

Confidence below `0.60` always calls `_ask_journey`, even with an active journey. `UNKNOWN` is generic reformulation only when a journey exists, otherwise menu. `HELP` optionally calls knowledge service, otherwise follows the same generic behavior. UNKNOWN, low confidence, and out-of-scope are not distinct capabilities.

## 10. Order History Capability

`get_customer_orders(customer)` exists in `orders/application/selectors.py`; the REST API has a customer-scoped readonly history endpoint/test. No conversational intent/handler calls it. “Donne-moi la liste de mes commandes” is therefore not a supported conversation capability and will be model-dependent `UNKNOWN`/items fallback.

## 11. Structured Actions vs Natural Language

| Capability | Structured action | Natural language | Same service? |
|---|---|---|---|
| Confirm | `order.confirm` | `CONFIRM` | Yes, `_confirm_active_order`/`confirm_order` |
| Cancel active draft | `order.cancel` | `CANCEL` | Yes, `abandon_order` -> `cancel_order` |
| Modify | `order.modify` | `MODIFY` | Partial; NL handler supports limited patterns |
| Remove item | none identified | `enlève/enleve/supprime …` under MODIFY | Yes |
| Replace item | none identified | only `remplace X par Y` | Yes when matched |
| Location | native location / location action | text address or `PROVIDE_LOCATION` | Partial |
| Resume/abandon/start new | actions exist in generic action handler | no dedicated intent | No |
| Status/history | `order.status` is emitted after confirmation | no supported handler | No |

## 12. Natural-Language Order Cancellation

If AI emits `CANCEL` with confidence ≥0.60 and no earlier short-circuit, `_cancel_active_order` calls `ActiveCustomerOrderService.abandon_order`. It locks conversation and order, validates customer ownership/mutable status, then uses `cancel_order(CUSTOMER_REQUEST)`. It cannot select an arbitrary LLM order ID. No test specifically proves the natural-language classification-to-cancel path.

## 13. Order Modification and Replacement Bug

**Root cause proved:** `_handle_modify` recognizes replacement only via `re.match(r"remplace X par Y")`. “À la place des arachides, je veux 1 L de maïs” does not match, then depends on AI `items`; targeting uses the extracted item name as target, which is likely “maïs”, not “arachides”. It returns “Je ne retrouve pas…” if maïs is absent.

Even in the regex path `target()` compares `requested_name.casefold()` or exact product name casefold. It does not normalize accents, aliases, singular/plural, units, or semantic references. Hence `arachides` does not match persisted `arachide`. Item UUID is backend-only and checked among that order’s items; no LLM UUID is trusted. Replacement retains old quantity/unit by design and is atomic through domain service transactions.

## 14. Workflow Interruption and Resume

Recovery can rebuild a draft, location request, pending clarification, or ready review from persisted order/context after a greeting. It has no suspension object, workflow stack, “return to order” intent, or order-history interrupt handler. A journey switch preserves state, but early route locks can consume unrelated messages. Minimum delta: represent a pending task separately from routing preference; classify interruptible intents before task-specific consumption; resume the persisted task only on explicit resume or after an interrupting capability completes.

## 15. Current ActiveCustomerOrder Semantics

`active_order_id` is a convenience pointer: `_validate_locked` parses it, locks the order, checks `customer_id` and status, and detaches stale/wrong-owner/terminal pointers. It does not search the user’s other orders. Exact active statuses are `DRAFT`, `AWAITING_LOCATION` (`DRAFT_MUTABLE_STATUSES`). After `CONFIRMED`, `CANCELLED`, or `FAILED`, the pointer is stale and detached on next validation; only confirmation clears full context immediately.

## 16. One Building Order Per User

**Is it enforced now? NO.** It is enforced only as “one pointer-reused mutable draft per conversation.” A new conversation or channel has no lookup of an existing user draft. No `Order` partial unique constraint exists for `(customer, status in DRAFT/AWAITING_LOCATION)`.

## 17. Concurrency Risks

Conversation and pointed order are `select_for_update` locked, so concurrent messages for the *same conversation* serialize draft selection. Different conversations for the same user do not share a lock; concurrent `get_or_create_draft` can each call `create_order_draft`. Reference uniqueness is unrelated. The desired invariant cannot currently be guaranteed transactionally or by DB.

## 18. Cross-Channel Behavior

**Proved and tested:** conversations are channel-identity scoped and `test_cross_channel_conversations_do_not_share_active_order` asserts they do not share a draft. Thus Telegram `DRAFT` followed by WhatsApp buy creates/uses WhatsApp’s separate draft; this violates the future user-scoped rule.

## 19. Catalog UNMATCHED Behavior

Grounding safely creates `UNMATCHED` with no invented product ID. `_handle_order_items` adds it, then asks location; it does not immediately offer “remove or replace unavailable product.” `OrderBuildingService.replace` is atomic and keeps old data if mutation fails, but it permits an unmatched replacement. Future touchpoints: item handler, clarification model/messages, replacement path, review/readiness and domain rule.

## 20. AMBIGUOUS Behavior

Ambiguous grounding is not added; the clarification engine writes `AMBIGUOUS_PRODUCT` with candidate IDs and asks a product-choice clarification. This is distinct technically from UNMATCHED, but there is no equivalent dedicated UNMATCHED conversational clarification.

## 21. Confirmation Readiness

**Proved:** current code already blocks both `AMBIGUOUS` and `UNMATCHED`: `OrderReviewService` adds `ambiguous_item`/`unmatched_items`, and `ensure_order_items_confirmable` requires `MATCHED` plus product. This is tested by `test_confirm_order_rejects_unmatched_and_ambiguous_items` and `test_review_exposes_missing_location_and_unmatched_as_blocker`. The pasted premise that UNMATCHED is warning-only is obsolete for this repository.

## 22. Capability Model Gap

The system has typed understanding and deterministic actions, but no explicit mapping of **understood intent -> supported/not-implemented/out-of-scope/social/unknown -> executable capability**. This causes understood-but-unimplemented requests (history/status/resume) to collapse into unknown/menu behavior.

## 23. Authorization / Ownership

Active-order mutations validate conversation user ownership and mutable status; item targeting queries only that order’s items. REST queries are customer-scoped. Natural `ORD-…`, “last order”, and explicit historical-order resolution are absent, rather than insecurely implemented. A raw `order.status` action is emitted but no generic action mapping was found for it, so it is not a proven authorized status flow.

## 24. Observability

Present logs include correlation ID in webhook/orchestrator paths, channel, message type, action ID, journey/flow in selected handlers, order ID/status at location persistence, and outbound requested/sent. Missing consistently: intent/confidence, route selected, active order status before routing, pending clarification, capability, outcome status/action list. Raw inbound payload and message text are persisted; default log calls inspected do not log text/GPS, but retention/access policy needs runtime review.

## 25. Existing Test Coverage

| Scenario | Existing test | Current evidence | Gap |
|---|---|---|---|
| Greeting/menu | orchestrator/journey tests | Covered | social classification runtime |
| Structured bypass | `test_structured_journey_actions_bypass_llm…` | Covered | order actions matrix partial |
| Active pointer | `test_creates_then_reuses…`, stale/owner tests | Covered | user-wide lookup absent |
| Cross channel | `test_cross_channel_conversations_do_not_share…` | Covered as current behavior | desired behavior untested |
| Clarification interruption | cancel/help/new-order tests | Partial | history/status/resume interruption absent |
| Replacement targeting | order-building unit test | IDs/index/ambiguity covered | plural/“à la place” absent |
| UNMATCHED/AMBIGUOUS confirm | order/review/grounding tests | Covered | conversational UNMATCHED UX absent |
| Duplicate building orders | none | Not covered | concurrency/invariant absent |
| History/status/out-of-scope | none | Not covered | absent capability |

## 26. End-to-End Scenario Matrix

| Scenario | Current route/result (proved except AI interpretation) |
|---|---|
| 1 Greeting | AI `GREETING` -> recovery/menu; no DB order mutation |
| 2 `1+1` | UNKNOWN/low confidence -> menu without journey; generic reformulation with journey |
| 3 `1 kg riz` | AI buy/items -> ground -> conversation draft -> item -> `AWAITING_LOCATION` + pending context |
| 4 Draft + add oil | Items -> same conversation pointer -> adds to A -> location/clarification flow |
| 5 Draft + new order | No dedicated intent; likely treated as item/buy and adds to A, not a controlled choice |
| 6 Draft + cancel | AI CANCEL -> abandon/cancel A -> pointer detached |
| 7 Draft + history | No history intent/handler; falls to UNKNOWN/menu/generic or item flow |
| 8 Draft + status | No status intent/handler; same gap |
| 9 Pending modify + replacement phrase | exact regex fails; extracted-item targeting can seek replacement as target -> not found |
| 10 Pending modify + greeting | GREETING triggers recovery, not social-only response |
| 11 Pending modify + resume | no resume intent; no explicit return-to-flow |
| 12 Telegram draft, WhatsApp buy | separate context -> separate draft possible |
| 13 Absent product | UNMATCHED item persisted, later confirmation blocked |
| 14 2 matched + 1 unmatched confirm | review/domain reject; recap includes blocker |

## 27. Root Causes

- **CRITICAL** — Building-order invariant is conversation-scoped with neither user-wide query nor partial unique constraint; cross-channel/concurrent duplicate drafts are possible.
- **HIGH** — Pre-AI state short-circuits turn unrelated messages into workflow input and prevent a natural interruption model.
- **HIGH** — No capability layer for history/status/resume/out-of-scope; understood requests degrade to menu/unknown.
- **HIGH** — Replacement grammar and exact target matching cause the observed failure.
- **MEDIUM** — Context has no task expiry/suspension semantics; `last_understanding` is unused.
- **MEDIUM** — UNMATCHED is safely blocked but lacks an explicit replace/remove dialogue.
- **LOW** — Decision observability is incomplete; raw payload persistence requires PII governance validation.

## 28. Minimal Architectural Delta

Keep the current boundary: NL -> typed understanding -> deterministic authorization/grounding -> existing domain service -> response plan. Add a small capability classification after understanding, before state-specific consumption where safe. Make pending task metadata resumable rather than a global route lock. Resolve active building orders by user from PostgreSQL; context should only cache/point to the resolved result. Extend item-reference normalization through deterministic catalog aliases/normalization, never model IDs.

## 29. Recommended Implementation Lots

1. **Capability classification and fallbacks:** social, out-of-scope, unknown, understood-not-implemented; add history/status/resume responses only where supported.
2. **Interruptible routing:** prioritize universal intents and persist/resume pending task explicitly.
3. **Natural order actions:** cancellation/status/history/resume and robust replacement/remove/add parsing all converge on current services.
4. **One building order invariant:** user-scoped selector, transaction/locking strategy, PostgreSQL partial unique constraint, cross-channel tests.
5. **Strict catalog UX:** unmatched remove/replace clarification and atomic re-grounding; retain confirm blocker.
6. **Observability and regression:** structured decision logs without raw PII; scenario and concurrency/runtime tests.

## 30. Final Verdict

AUDIT_COMPLETE

RUNTIME_VALIDATION_REQUIRED: AI classifications for the French example phrases, provider configuration/availability, actual database migration state, and production log/payload retention cannot be proven from static source alone.
