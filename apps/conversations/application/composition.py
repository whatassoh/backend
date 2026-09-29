"""Build and execute the presentation-only response composition boundary."""

import hashlib
import logging
from dataclasses import asdict, replace

from apps.ai.application.composer import NaturalResponseComposer
from apps.ai.application.response_evaluation import evaluate_response
from apps.ai.application.service import AIService
from apps.ai.domain.response import ResponseBrief
from apps.conversations.application.context_window import (
    ConversationContextWindowBuilder,
)
from apps.conversations.application.interruptions import active_task_kind
from apps.conversations.application.memory import ConversationMemoryService
from apps.conversations.application.response_policies import select_response_policy
from apps.conversations.application.style import style_snapshot
from apps.conversations.domain.context import ConversationContext
from apps.conversations.infrastructure.models import Conversation

logger = logging.getLogger(__name__)


def compose_outcome_safely(
    *,
    conversation: Conversation,
    outcome,
    enabled: bool,
    shadow: bool,
    correlation_id=None,
):
    """Rephrase after business execution; every failure returns the original outcome."""

    if (
        not (enabled or shadow)
        or outcome.status != "PROCESSED"
        or not outcome.response.text
    ):
        return outcome
    context = ConversationContext.from_payload(conversation.context)
    task = active_task_kind(context)
    policy = select_response_policy(outcome, context)
    if not policy.composable:
        return outcome
    dialogue_context = ConversationContextWindowBuilder().build(conversation, context)
    brief = ResponseBrief(
        objective=policy.objective,
        authoritative_text=outcome.response.text,
        style=style_snapshot(conversation),
        channel=conversation.channel,
        active_task=task.value if task else None,
        valid_options=tuple(action.label for action in outcome.response.actions),
        required_facts=tuple(_memory_preferences(conversation)),
        forbidden_claims=(
            "unexecuted action",
            "unverified price",
            "unverified stock",
            "unverified location",
            "permission inferred from memory",
        ),
        use_case=policy.use_case.value,
        required_speech_acts=policy.required_speech_acts,
        forbidden_speech_acts=policy.forbidden_speech_acts,
        max_characters=policy.max_characters,
        preserve_question=policy.preserve_question,
        preserve_line_structure=policy.preserve_line_structure,
        require_location_reference=policy.require_location_reference,
        metadata={
            "greeting_count": context.social_state.greeting_count,
            "introduced": bool(context.social_state.introduced_at),
            "recent_messages": dialogue_context.get("recent_messages", [])[-6:],
        },
    )
    accepted = False
    candidate = outcome.response
    audit_candidate = outcome.response
    evaluation_data = {}
    reason = "accepted"
    try:
        composer = NaturalResponseComposer(AIService())
        candidate, accepted = composer.compose(
            brief, outcome.response, correlation_id=correlation_id
        )
        if composer.last_candidate is not None:
            audit_candidate = replace(outcome.response, text=composer.last_candidate)
        if accepted:
            previous = (
                conversation.messages.filter(direction="OUTBOUND")
                .order_by("-occurred_at", "-created_at")
                .values_list("text", flat=True)
                .first()
                or ""
            )
            evaluation = evaluate_response(
                baseline=outcome.response.text,
                candidate=candidate.text,
                style=brief.style,
                previous_text=previous,
            )
            evaluation_data = asdict(evaluation)
            if not evaluation.passes():
                accepted = False
                candidate = outcome.response
                reason = "evaluation_gate"
        if not accepted:
            reason = "mechanical_fact_guard" if reason == "accepted" else reason
    except Exception as exc:  # Presentation failures must never replace a valid answer.
        reason = exc.__class__.__name__
        logger.exception(
            "natural_response_composer_failed",
            extra={
                "conversation_id": str(conversation.id),
                "correlation_id": correlation_id,
            },
        )
    try:
        _record_composition(
            conversation=conversation,
            correlation_id=correlation_id,
            baseline=outcome.response.text,
            candidate=candidate.text,
            generated_candidate=audit_candidate.text,
            style=brief.style,
            use_case=brief.use_case,
            evaluation=evaluation_data,
            accepted=accepted,
            reason=reason,
            shadow=shadow,
        )
    except Exception:
        logger.exception(
            "natural_response_composition_audit_failed",
            extra={"conversation_id": str(conversation.id)},
        )
    return replace(outcome, response=candidate) if enabled and accepted else outcome


def _memory_preferences(conversation):
    return (
        f"communication preference {key}={value}"
        for key, value in ConversationMemoryService()
        .safe_context(conversation.user)
        .items()
    )


def _record_composition(**values):
    from apps.ai.infrastructure.models import ResponseCompositionRun

    baseline = values.pop("baseline")
    candidate = values.pop("candidate")
    generated_candidate = values.pop("generated_candidate")
    ResponseCompositionRun.objects.update_or_create(
        conversation=values.pop("conversation"),
        correlation_id=values.pop("correlation_id") or None,
        defaults={
            **values,
            "baseline_text": baseline,
            "candidate_text": generated_candidate,
            "baseline_hash": hashlib.sha256(baseline.encode()).hexdigest(),
            "candidate_hash": hashlib.sha256(generated_candidate.encode()).hexdigest(),
            "delivered_candidate_hash": hashlib.sha256(candidate.encode()).hexdigest(),
        },
    )
