"""Fast, deterministic communication-style analysis over versioned resources."""

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

EMOJI_PATTERN = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf]", re.UNICODE)
FRENCH_CLUES = frozenset(
    "alors avec bonjour car ce cette dans des donc elle encore est et je la le les mais merci moi mon nous ou pas pour que qui sans suis sur toi tu une votre vous".split()
)
ENGLISH_CLUES = frozenset(
    "and are can could do for from hello help how i is it me my not of on please that the this to we what with would you your".split()
)
TU_MARKERS = frozenset("tu toi ton ta tes te t tutoie".split())
VOUS_MARKERS = frozenset("vous votre vos veuillez vouvoie".split())
WARM_MARKERS = frozenset(
    "bonjour bonsoir coucou salut hey merci plaisir super sympa welcome thanks thank hello hi".split()
)
URGENCY_MARKERS = frozenset(
    "urgent urgence immédiatement immediatement maintenant rapidement vite asap emergency".split()
)


class StyleLexiconError(RuntimeError):
    """The shipped style resource is malformed or has lost integrity."""


@dataclass(frozen=True, slots=True)
class StyleMarker:
    text: str
    locale: str
    category: str
    weight: float


@dataclass(frozen=True, slots=True)
class StyleLexicon:
    version: str
    marker_count: int
    casual_threshold: float
    markers_by_size: dict[int, dict[str, StyleMarker]]
    formal_markers: frozenset[str]
    technical_terms: frozenset[str]
    maximum_ngram: int


@dataclass(frozen=True, slots=True)
class StyleSignalReport:
    characters: int
    words: int
    casual_score: float
    formal_score: float
    technical_score: float
    tu_score: float
    vous_score: float
    warmth_score: float
    urgency_score: float
    emoji_count: int
    exclamation_count: int
    question_count: int
    uppercase_ratio: float
    language: str | None
    language_confidence: float
    evidence_count: int
    lexicon_version: str


@lru_cache(maxsize=1)
def load_style_lexicon() -> StyleLexicon:
    resource = files("apps.conversations.resources").joinpath("casual_markers.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    expected_hash = payload.pop("content_sha256", None)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    actual_hash = hashlib.sha256(canonical.encode()).hexdigest()
    if expected_hash != actual_hash:
        raise StyleLexiconError("Style lexicon integrity check failed.")
    markers = payload.get("markers")
    if not isinstance(markers, list) or len(markers) < 1000:
        raise StyleLexiconError("Style lexicon must contain at least 1000 markers.")
    by_size: dict[int, dict[str, StyleMarker]] = {}
    seen: set[str] = set()
    for value in markers:
        marker = StyleMarker(
            text=str(value["text"]),
            locale=str(value["locale"]),
            category=str(value["category"]),
            weight=float(value["weight"]),
        )
        if marker.text in seen or not 0 < marker.weight <= 5:
            raise StyleLexiconError("Style lexicon contains duplicate or invalid data.")
        seen.add(marker.text)
        size = len(marker.text.split())
        by_size.setdefault(size, {})[marker.text] = marker
    if payload.get("marker_count") != len(markers):
        raise StyleLexiconError("Style lexicon marker count is inconsistent.")
    return StyleLexicon(
        version=str(payload["version"]),
        marker_count=len(markers),
        casual_threshold=float(payload["casual_threshold"]),
        markers_by_size=by_size,
        formal_markers=frozenset(payload["formal_markers"]),
        technical_terms=frozenset(payload["technical_terms"]),
        maximum_ngram=max(by_size),
    )


def analyze_writing_style(text: str) -> StyleSignalReport:
    lexicon = load_style_lexicon()
    normalized = normalize_style_text(text)
    tokens = normalized.split()
    markers = _non_overlapping_markers(tokens, lexicon)
    words = set(tokens)
    language, language_confidence = _language(words, markers)
    technical_score = sum(
        1.0 for term in lexicon.technical_terms if _phrase_present(term, normalized)
    )
    formal_score = sum(
        2.0 for marker in lexicon.formal_markers if _phrase_present(marker, normalized)
    )
    uppercase_letters = sum(character.isupper() for character in text)
    letters = sum(character.isalpha() for character in text)
    return StyleSignalReport(
        characters=len(text),
        words=len(tokens),
        casual_score=round(sum(marker.weight for marker in markers), 3),
        formal_score=formal_score,
        technical_score=technical_score,
        tu_score=float(len(words & TU_MARKERS)),
        vous_score=float(len(words & VOUS_MARKERS)),
        warmth_score=float(len(words & WARM_MARKERS)),
        urgency_score=float(len(words & URGENCY_MARKERS)),
        emoji_count=len(EMOJI_PATTERN.findall(text)),
        exclamation_count=text.count("!"),
        question_count=text.count("?"),
        uppercase_ratio=round(uppercase_letters / letters, 3) if letters else 0.0,
        language=language,
        language_confidence=language_confidence,
        evidence_count=len(markers),
        lexicon_version=lexicon.version,
    )


def normalize_style_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", value.casefold()).split())


def _non_overlapping_markers(tokens, lexicon):
    matches: list[StyleMarker] = []
    cursor = 0
    while cursor < len(tokens):
        matched = None
        maximum = min(lexicon.maximum_ngram, len(tokens) - cursor)
        for size in range(maximum, 0, -1):
            phrase = " ".join(tokens[cursor : cursor + size])
            marker = lexicon.markers_by_size.get(size, {}).get(phrase)
            if marker is not None:
                matched = marker
                cursor += size
                matches.append(marker)
                break
        if matched is None:
            cursor += 1
    return tuple(matches)


def _language(words, markers):
    french = len(words & FRENCH_CLUES) + sum(
        marker.weight for marker in markers if marker.locale == "fr"
    )
    english = len(words & ENGLISH_CLUES) + sum(
        marker.weight for marker in markers if marker.locale == "en"
    )
    total = french + english
    if total < 2 or math.isclose(french, english, abs_tol=0.5):
        return None, 0.0
    selected = "fr" if french > english else "en"
    confidence = max(french, english) / total
    return selected, round(confidence, 3)


def _phrase_present(phrase, normalized):
    return bool(re.search(rf"(?:^|\s){re.escape(phrase)}(?:\s|$)", normalized))
