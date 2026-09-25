"""Validated multilingual extraction of explicit communication preferences."""

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

from apps.conversations.application.style_engine import normalize_style_text

ALLOWED_FACT_VALUES = {
    "address_form": {"tu", "vous"},
    "response_length": {"short", "detailed"},
    "emoji_preference": {"none", "light"},
    "preferred_language": {"fr", "en"},
    "technical_level": {"simple", "technical"},
}


class PreferenceLexiconError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExplicitPreference:
    fact_type: str
    value: str
    locale: str
    evidence_hash: str
    lexicon_version: str


@dataclass(frozen=True, slots=True)
class PreferenceEntry:
    fact_type: str
    value: str
    locale: str
    text: str


@dataclass(frozen=True, slots=True)
class PreferenceLexicon:
    version: str
    entries_by_size: dict[int, dict[str, tuple[PreferenceEntry, ...]]]
    maximum_ngram: int


@lru_cache(maxsize=1)
def load_preference_lexicon() -> PreferenceLexicon:
    resource = files("apps.conversations.resources").joinpath(
        "communication_preferences.json"
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    expected_hash = payload.pop("content_sha256", None)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if expected_hash != hashlib.sha256(canonical.encode()).hexdigest():
        raise PreferenceLexiconError("Preference lexicon integrity check failed.")
    entries = payload.get("entries")
    if not isinstance(entries, list) or payload.get("entry_count") != len(entries):
        raise PreferenceLexiconError("Preference lexicon count is inconsistent.")
    mutable: dict[int, dict[str, list[PreferenceEntry]]] = {}
    for value in entries:
        entry = PreferenceEntry(
            fact_type=str(value["fact_type"]),
            value=str(value["value"]),
            locale=str(value["locale"]),
            text=str(value["text"]),
        )
        if entry.value not in ALLOWED_FACT_VALUES.get(entry.fact_type, set()):
            raise PreferenceLexiconError(
                "Preference lexicon contains a forbidden fact."
            )
        size = len(entry.text.split())
        mutable.setdefault(size, {}).setdefault(entry.text, []).append(entry)
    by_size = {
        size: {text: tuple(group) for text, group in values.items()}
        for size, values in mutable.items()
    }
    return PreferenceLexicon(
        version=str(payload["version"]),
        entries_by_size=by_size,
        maximum_ngram=max(by_size),
    )


def extract_explicit_preferences(text: str | None) -> tuple[ExplicitPreference, ...]:
    normalized = normalize_style_text(text or "")
    if not normalized:
        return ()
    lexicon = load_preference_lexicon()
    tokens = normalized.split()
    matches: dict[str, list[PreferenceEntry]] = {}
    for size in range(1, min(lexicon.maximum_ngram, len(tokens)) + 1):
        index = lexicon.entries_by_size.get(size, {})
        for start in range(0, len(tokens) - size + 1):
            phrase = " ".join(tokens[start : start + size])
            for entry in index.get(phrase, ()):
                matches.setdefault(entry.fact_type, []).append(entry)
    preferences = []
    for fact_type, candidates in matches.items():
        values = {candidate.value for candidate in candidates}
        if len(values) != 1:
            continue
        selected = max(candidates, key=lambda item: len(item.text.split()))
        preferences.append(
            ExplicitPreference(
                fact_type=fact_type,
                value=selected.value,
                locale=selected.locale,
                evidence_hash=hashlib.sha256(selected.text.encode()).hexdigest(),
                lexicon_version=lexicon.version,
            )
        )
    return tuple(sorted(preferences, key=lambda item: item.fact_type))
