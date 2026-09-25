"""Universal interruption policy shared by every conversational workflow.

The functions in this module only classify navigation and transform temporary
conversation state. They never mutate an order, store, catalog, or driver row.
"""

import re
import unicodedata
from dataclasses import dataclass, replace
from enum import StrEnum

from django.utils import timezone

from apps.conversations.domain.context import (
    ActiveJourney,
    ConversationContext,
    ConversationFlow,
    ConversationTaskKind,
    DriverOnboardingStage,
    SellerOnboardingStage,
    SuspendedConversationTask,
)


class UniversalCommand(StrEnum):
    CANCEL = "CANCEL"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    BACK = "BACK"
    HELP = "HELP"
    GREETING = "GREETING"
    MEMORY_SHOW = "MEMORY_SHOW"
    MEMORY_FORGET = "MEMORY_FORGET"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResumeDecision:
    context: ConversationContext
    task: SuspendedConversationTask | None


_COMMAND_PATTERNS = {
    UniversalCommand.CANCEL: (
        r"annul(?:e|er|ez)?",
        r"stop",
        r"arrete(?:r|z)?",
        r"laisse tomber",
    ),
    UniversalCommand.PAUSE: (
        r"pause",
        r"plus tard",
        r"on reprend plus tard",
        r"garde ca pour plus tard",
    ),
    UniversalCommand.RESUME: (
        r"reprend(?:s|re|ez)?",
        r"continue(?:r|z)?",
        r"on continue",
        r"reviens? a ca",
    ),
    UniversalCommand.BACK: (
        r"retour",
        r"revenir en arriere",
        r"etape precedente",
        r"precedent",
    ),
    UniversalCommand.HELP: (r"aide", r"help", r"que peux tu faire"),
    UniversalCommand.GREETING: (
        r"bonjour",
        r"bonsoir",
        r"salut",
        r"coucou",
        r"hello",
        r"bon matin",
    ),
    UniversalCommand.MEMORY_SHOW: (
        r"qu est ce que tu sais sur moi",
        r"que sais tu sur moi",
        r"qu as tu memorise sur moi",
        r"montre mes preferences",
    ),
    UniversalCommand.MEMORY_FORGET: (
        r"oublie mes preferences",
        r"oublie ce que tu sais sur moi",
        r"efface ta memoire sur moi",
        r"supprime mes preferences",
    ),
}


def detect_universal_command(text: str | None) -> UniversalCommand | None:
    """Recognize only standalone navigation phrases with high precision."""

    normalized = _normalize(text)
    if not normalized:
        return None
    for command, patterns in _COMMAND_PATTERNS.items():
        if any(re.fullmatch(pattern, normalized) for pattern in patterns):
            return command
    return None


def active_task_kind(context: ConversationContext) -> ConversationTaskKind | None:
    """Resolve the foreground task without trusting model output."""

    if context.metadata.get("task_paused"):
        return None
    if context.active_journey == ActiveJourney.SELL:
        if (
            context.seller_onboarding is not None
            and context.seller_onboarding.stage != SellerOnboardingStage.COMPLETED
        ):
            return ConversationTaskKind.SELLER_ONBOARDING
        return ConversationTaskKind.SELLER_CATALOG
    if context.active_journey == ActiveJourney.DELIVER:
        if (
            context.driver_onboarding is not None
            and context.driver_onboarding.stage != DriverOnboardingStage.COMPLETED
        ):
            return ConversationTaskKind.DRIVER_ONBOARDING
        return ConversationTaskKind.JOURNEY
    if context.metadata.get("awaiting_delivery_instructions"):
        return ConversationTaskKind.DELIVERY_INSTRUCTIONS
    if context.active_order_id or context.pending_clarification:
        return ConversationTaskKind.ORDER
    if context.active_journey is not None:
        return ConversationTaskKind.JOURNEY
    return None


def has_resumable_work(context: ConversationContext) -> bool:
    return active_task_kind(context) is not None or bool(context.suspended_tasks)


def suspend_active_task(
    context: ConversationContext, *, reason: str
) -> tuple[ConversationContext, SuspendedConversationTask | None]:
    """Suspend navigation while retaining typed workflow/domain references."""

    kind = active_task_kind(context)
    if kind is None:
        return context, None
    task = SuspendedConversationTask(
        kind=kind,
        journey=context.active_journey,
        flow=context.flow,
        suspended_at=timezone.now().isoformat(),
        reason=reason,
    )
    tasks = tuple(item for item in context.suspended_tasks if item.kind != kind)
    tasks = (*tasks, task)[-5:]
    metadata = {**context.metadata, "task_paused": True}
    return (
        replace(
            context,
            flow=ConversationFlow.IDLE,
            active_journey=None,
            suspended_tasks=tasks,
            metadata=metadata,
        ),
        task,
    )


def resume_last_task(context: ConversationContext) -> ResumeDecision:
    if not context.suspended_tasks:
        return ResumeDecision(context=context, task=None)
    task = context.suspended_tasks[-1]
    current_kind = active_task_kind(context)
    remaining = context.suspended_tasks[:-1]
    if current_kind is not None and current_kind != task.kind:
        current = SuspendedConversationTask(
            kind=current_kind,
            journey=context.active_journey,
            flow=context.flow,
            suspended_at=timezone.now().isoformat(),
            reason="resume_switch",
        )
        remaining = tuple(item for item in remaining if item.kind != current_kind)
        remaining = (*remaining, current)[-5:]
    metadata = {**context.metadata}
    metadata.pop("task_paused", None)
    return ResumeDecision(
        context=replace(
            context,
            flow=task.flow,
            active_journey=task.journey,
            suspended_tasks=remaining,
            metadata=metadata,
        ),
        task=task,
    )


def discard_suspended_kind(
    context: ConversationContext, kind: ConversationTaskKind
) -> ConversationContext:
    return replace(
        context,
        suspended_tasks=tuple(
            task for task in context.suspended_tasks if task.kind != kind
        ),
    )


def _normalize(text: str | None) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-z0-9\s]", " ", value.casefold())
    return " ".join(value.split())
