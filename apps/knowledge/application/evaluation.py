"""Deterministic retrieval metrics used as a release gate for RAG changes."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class RetrievalEvaluationCase:
    case_id: str
    query: str
    expected_source_keys: tuple[str, ...] = ()
    forbidden_source_keys: tuple[str, ...] = ()
    expect_no_answer: bool = False
    language: str = "fr"
    namespace: str = "customer-help"


@dataclass(frozen=True, slots=True, kw_only=True)
class RetrievalEvaluationReport:
    metrics: dict[str, float | int]
    cases: tuple[dict, ...]


class RetrievalEvaluator:
    def evaluate(self, cases, *, retrieve) -> RetrievalEvaluationReport:
        details = []
        reciprocal_ranks = []
        recalls = []
        seen_case_ids = set()
        hits = 0
        answerable_hits = 0
        answerable_count = 0
        leaks = 0
        correct_abstentions = 0
        abstention_count = 0
        for case in cases:
            if not case.case_id or case.case_id in seen_case_ids:
                raise ValueError("Evaluation case IDs must be non-empty and unique.")
            seen_case_ids.add(case.case_id)
            if not case.query.strip():
                raise ValueError("Evaluation queries cannot be empty.")
            if case.expect_no_answer == bool(case.expected_source_keys):
                raise ValueError(
                    "Each evaluation case must expect sources or an abstention."
                )
            if set(case.expected_source_keys).intersection(case.forbidden_source_keys):
                raise ValueError("Expected and forbidden sources cannot overlap.")
            result = retrieve(case)
            keys = [chunk.source_key for chunk in result.chunks]
            expected = set(case.expected_source_keys)
            forbidden = set(case.forbidden_source_keys)
            matched = expected.intersection(keys)
            recall = len(matched) / len(expected) if expected else None
            rank = next(
                (index for index, key in enumerate(keys, start=1) if key in expected),
                None,
            )
            abstained = not keys
            hit = bool(matched) if expected else abstained
            leaked = bool(forbidden.intersection(keys))
            hits += int(hit)
            leaks += int(leaked)
            if expected:
                answerable_count += 1
                answerable_hits += int(hit)
                recalls.append(recall)
                reciprocal_ranks.append(1 / rank if rank else 0.0)
            else:
                abstention_count += 1
                correct_abstentions += int(abstained)
            details.append(
                {
                    "case_id": case.case_id,
                    "hit": hit,
                    "recall": recall,
                    "reciprocal_rank": 1 / rank if rank else 0.0,
                    "forbidden_leak": leaked,
                    "abstained": abstained,
                    "retrieved_source_keys": keys,
                    "strategy": result.strategy.value,
                }
            )
        count = len(details)
        metrics = {
            "case_count": count,
            "hit_rate": hits / count if count else 0.0,
            "answerable_case_count": answerable_count,
            "answerable_hit_rate": (
                answerable_hits / answerable_count if answerable_count else 0.0
            ),
            "mean_recall": (
                sum(recalls) / answerable_count if answerable_count else 0.0
            ),
            "mean_reciprocal_rank": (
                sum(reciprocal_ranks) / answerable_count if answerable_count else 0.0
            ),
            "forbidden_leakage_rate": leaks / count if count else 0.0,
            "abstention_case_count": abstention_count,
            "correct_abstention_count": correct_abstentions,
            "abstention_accuracy": (
                correct_abstentions / abstention_count if abstention_count else 1.0
            ),
        }
        return RetrievalEvaluationReport(metrics=metrics, cases=tuple(details))
