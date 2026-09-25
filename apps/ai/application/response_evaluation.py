"""Deterministic rollout gates for natural response candidates."""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResponseEvaluation:
    factual_shape: float
    repetition: float
    context_preservation: float
    tone_alignment: float

    def passes(
        self, *, factual_min=1.0, repetition_max=0.85, context_min=0.8, tone_min=0.8
    ):
        return (
            self.factual_shape >= factual_min
            and self.repetition <= repetition_max
            and self.context_preservation >= context_min
            and self.tone_alignment >= tone_min
        )


def evaluate_response(*, baseline: str, candidate: str, style: dict, previous_text=""):
    """Score machine-checkable invariants; semantic evals remain a release gate."""

    baseline_numbers = _tokens(r"\b\d+(?:[.,]\d+)?\b", baseline)
    candidate_numbers = _tokens(r"\b\d+(?:[.,]\d+)?\b", candidate)
    baseline_urls = _tokens(r"https?://\S+", baseline)
    candidate_urls = _tokens(r"https?://\S+", candidate)
    factual = float(
        baseline_numbers == candidate_numbers and baseline_urls == candidate_urls
    )
    repetition = (
        _jaccard(_words(previous_text), _words(candidate)) if previous_text else 0.0
    )
    context = _required_token_recall(baseline_numbers | baseline_urls, candidate)
    tone = _tone_score(candidate, style)
    return ResponseEvaluation(factual, repetition, context, tone)


def _tone_score(text, style):
    checks = []
    lowered = text.casefold()
    address = style.get("address_form")
    if address == "tu":
        checks.append(not bool(re.search(r"\b(vous|votre|vos)\b", lowered)))
    elif address == "vous":
        checks.append(not bool(re.search(r"\b(tu|toi|ton|ta|tes)\b", lowered)))
    if style.get("emoji_level") == "none":
        checks.append(
            not bool(re.search("[\U0001f300-\U0001faff\U00002600-\U000027bf]", text))
        )
    if style.get("verbosity") == "concise":
        checks.append(len(text) <= 500)
    return sum(checks) / len(checks) if checks else 1.0


def _required_token_recall(required, candidate):
    if not required:
        return 1.0
    present = _tokens(r"\b\d+(?:[.,]\d+)?\b", candidate) | _tokens(
        r"https?://\S+", candidate
    )
    return len(required & present) / len(required)


def _tokens(pattern, text):
    return set(re.findall(pattern, text))


def _words(text):
    return set(re.findall(r"\w+", text.casefold()))


def _jaccard(left, right):
    return len(left & right) / len(left | right) if left or right else 0.0
