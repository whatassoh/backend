"""Governed conversational memory.

Only explicit, low-sensitivity communication preferences are durable here.
Orders, prices, inventory, locations, identities and permissions remain owned by
their authoritative domain models and must never be inferred from this memory.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.conversations.infrastructure.models import (
    Conversation,
    ConversationEpisode,
    ConversationStyleProfile,
    ConversationSummary,
    MemoryFact,
    UserCommunicationProfile,
)
from apps.conversations.application.preference_engine import (
    extract_explicit_preferences,
)

_STYLE_FIELDS = {
    "address_form": "address_form",
    "response_length": "verbosity",
    "emoji_preference": "emoji_level",
    "preferred_language": "language",
    "technical_level": "technical_level",
}


class ConversationMemoryService:
    """Write and read allowlisted social preferences with provenance and expiry."""

    @transaction.atomic
    def remember_explicit_preferences(
        self,
        conversation: Conversation,
        text: str | None,
        *,
        source_message_id: str | None,
    ) -> tuple[MemoryFact, ...]:
        preferences = extract_explicit_preferences(text)
        if not preferences:
            return ()
        # Serialize all preference updates for one user, including first writes where
        # no MemoryFact row exists yet to lock.
        conversation.user.__class__.objects.select_for_update().get(
            pk=conversation.user_id
        )
        expires_at = timezone.now() + timedelta(days=settings.AI_MEMORY_FACT_TTL_DAYS)
        stored = []
        for preference in preferences:
            active = list(
                MemoryFact.objects.select_for_update()
                .filter(
                    user=conversation.user,
                    fact_type=preference.fact_type,
                    superseded_by__isnull=True,
                    deleted_at__isnull=True,
                )
                .order_by("-created_at")
            )
            if active and active[0].value == preference.value:
                stored.append(active[0])
                continue
            fact = MemoryFact.objects.create(
                user=conversation.user,
                conversation=conversation,
                fact_type=preference.fact_type,
                value=preference.value,
                provenance={
                    "source": "explicit_user_statement",
                    "message_id": source_message_id,
                    "evidence_hash": preference.evidence_hash,
                    "lexicon_version": preference.lexicon_version,
                    "locale": preference.locale,
                },
                confidence="1.000",
                sensitivity="LOW",
                consent="EXPLICIT",
                expires_at=expires_at,
            )
            MemoryFact.objects.filter(pk__in=[item.pk for item in active]).update(
                superseded_by=fact
            )
            stored.append(fact)
            self._apply_to_style(conversation, preference)
        return tuple(stored)

    def active_facts(self, user) -> tuple[MemoryFact, ...]:
        now = timezone.now()
        return tuple(
            MemoryFact.objects.filter(
                user=user,
                superseded_by__isnull=True,
                deleted_at__isnull=True,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .order_by("fact_type", "-created_at")
        )

    def safe_context(self, user) -> dict[str, str]:
        return {fact.fact_type: str(fact.value) for fact in self.active_facts(user)}

    @transaction.atomic
    def forget(self, user) -> int:
        user.__class__.objects.select_for_update().get(pk=user.pk)
        now = timezone.now()
        count = MemoryFact.objects.filter(user=user, deleted_at__isnull=True).update(
            deleted_at=now
        )
        ConversationStyleProfile.objects.filter(conversation__user=user).delete()
        UserCommunicationProfile.objects.filter(user=user).delete()
        ConversationSummary.objects.filter(conversation__user=user).delete()
        ConversationEpisode.objects.filter(conversation__user=user).delete()
        return count

    @staticmethod
    def describe(user) -> str:
        facts = ConversationMemoryService().safe_context(user)
        if not facts:
            return (
                "Je n’ai enregistré aucune préférence de conversation durable "
                "à votre sujet."
            )
        labels = {
            "address_form": "forme d’adresse",
            "response_length": "longueur des réponses",
            "emoji_preference": "préférence d’emojis",
            "preferred_language": "langue préférée",
            "technical_level": "niveau technique",
        }
        values = ", ".join(
            f"{labels[key]} : {value}" for key, value in sorted(facts.items())
        )
        return f"J’ai uniquement mémorisé ces préférences de conversation : {values}."

    @staticmethod
    def _apply_to_style(conversation, preference):
        profile, _ = ConversationStyleProfile.objects.select_for_update().get_or_create(
            conversation=conversation
        )
        field = _STYLE_FIELDS[preference.fact_type]
        setattr(profile, field, preference.value)
        profile.save(update_fields=[field, "updated_at"])
