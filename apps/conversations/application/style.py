"""Persistent style profiles built from aggregate, low-sensitivity evidence."""

from django.db import transaction

from apps.conversations.application.style_engine import (
    StyleSignalReport,
    analyze_writing_style,
    load_style_lexicon,
)
from apps.conversations.infrastructure.models import (
    ConversationStyleProfile,
    UserCommunicationProfile,
)

DEFAULT_STYLE = {
    "language": "fr",
    "register": "neutral",
    "address_form": "vous",
    "verbosity": "balanced",
    "warmth": "warm",
    "emoji_level": "none",
    "technical_level": "simple",
}
PROFILE_FIELDS = tuple(DEFAULT_STYLE)


def style_snapshot(conversation):
    """Resolve explicit preferences over local and cross-channel observations."""

    snapshot = dict(DEFAULT_STYLE)
    user_profile = UserCommunicationProfile.objects.filter(
        user_id=conversation.user_id
    ).first()
    local_profile = ConversationStyleProfile.objects.filter(
        conversation=conversation
    ).first()
    if user_profile is not None:
        snapshot.update(_profile_values(user_profile))
    if local_profile is not None and local_profile.sample_count >= 2:
        snapshot.update(_profile_values(local_profile))

    from apps.conversations.application.memory import ConversationMemoryService

    preferences = ConversationMemoryService().safe_context(conversation.user)
    overrides = {
        "preferred_language": ("language", preferences.get("preferred_language")),
        "address_form": ("address_form", preferences.get("address_form")),
        "response_length": ("verbosity", preferences.get("response_length")),
        "emoji_preference": ("emoji_level", preferences.get("emoji_preference")),
        "technical_level": ("technical_level", preferences.get("technical_level")),
    }
    for field, value in overrides.values():
        if value is not None:
            snapshot[field] = "concise" if value == "short" else value
    return snapshot


@transaction.atomic
def observe_writing_style(conversation, text):
    """Update conversation and user aggregates without retaining the message."""

    if not isinstance(text, str) or not text.strip():
        return None
    report = analyze_writing_style(text)
    user_model = conversation.user.__class__
    user_model.objects.select_for_update().get(pk=conversation.user_id)
    global_profile, _ = (
        UserCommunicationProfile.objects.select_for_update().get_or_create(
            user_id=conversation.user_id
        )
    )
    local_profile, _ = (
        ConversationStyleProfile.objects.select_for_update().get_or_create(
            conversation=conversation
        )
    )
    _apply_report(global_profile, report)
    _apply_report(local_profile, report)
    return local_profile


def _apply_report(profile, report: StyleSignalReport):
    signals = dict(profile.signals or {})
    for name in (
        "characters",
        "words",
        "casual_score",
        "formal_score",
        "technical_score",
        "tu_score",
        "vous_score",
        "warmth_score",
        "urgency_score",
        "emoji_count",
        "exclamation_count",
        "question_count",
        "evidence_count",
    ):
        signals[name] = round(
            float(signals.get(name, 0)) + float(getattr(report, name)), 3
        )
    signals["messages"] = int(signals.get("messages", 0)) + 1
    signals["uppercase_ratio_sum"] = round(
        float(signals.get("uppercase_ratio_sum", 0)) + report.uppercase_ratio, 3
    )
    signals["lexicon_version"] = report.lexicon_version
    language_scores = dict(signals.get("language_scores") or {})
    if report.language:
        language_scores[report.language] = round(
            float(language_scores.get(report.language, 0)) + report.language_confidence,
            3,
        )
    signals["language_scores"] = language_scores
    profile.signals = signals
    profile.sample_count = signals["messages"]
    _derive_profile(profile, signals)
    profile.save()


def _derive_profile(profile, signals):
    messages = max(int(signals["messages"]), 1)
    lexicon = load_style_lexicon()
    casual = float(signals["casual_score"])
    formal = float(signals["formal_score"])
    if casual >= lexicon.casual_threshold and casual > formal * 1.2:
        profile.register = "casual"
    elif formal >= 3 and formal > casual * 1.2:
        profile.register = "formal"
    else:
        profile.register = "neutral"

    tu_score = float(signals["tu_score"])
    vous_score = float(signals["vous_score"])
    if tu_score > vous_score:
        profile.address_form = "tu"
    elif vous_score > tu_score:
        profile.address_form = "vous"

    average_length = float(signals["characters"]) / messages
    profile.verbosity = (
        "concise"
        if average_length < 55
        else "detailed"
        if average_length > 240
        else "balanced"
    )
    emoji_rate = float(signals["emoji_count"]) / messages
    profile.emoji_level = (
        "frequent" if emoji_rate >= 1.5 else "light" if emoji_rate >= 0.25 else "none"
    )
    profile.technical_level = (
        "technical"
        if float(signals["technical_score"]) >= max(2, messages * 0.35)
        else "simple"
    )
    profile.warmth = (
        "expressive"
        if float(signals["warmth_score"]) >= messages
        else "warm"
        if float(signals["warmth_score"]) > 0
        else "neutral"
    )
    language_scores = signals.get("language_scores") or {}
    if language_scores:
        profile.language = max(language_scores, key=language_scores.get)
    if hasattr(profile, "confidence"):
        total_register = casual + formal
        total_address = tu_score + vous_score
        total_language = sum(language_scores.values())
        profile.confidence = {
            "register": round(max(casual, formal) / total_register, 3)
            if total_register
            else 0.0,
            "address_form": round(max(tu_score, vous_score) / total_address, 3)
            if total_address
            else 0.0,
            "language": round(max(language_scores.values()) / total_language, 3)
            if total_language
            else 0.0,
        }


def _profile_values(profile):
    return {field: getattr(profile, field) for field in PROFILE_FIELDS}
