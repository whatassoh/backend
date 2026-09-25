from dataclasses import replace
from uuid import uuid4

from apps.knowledge.application.evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluator,
)
from apps.knowledge.domain.dtos import (
    KnowledgeChunkResult,
    KnowledgeRetrievalResult,
    KnowledgeRetrievalStrategy,
)


def result_for(*keys):
    base = KnowledgeChunkResult(
        chunk_id=uuid4(),
        source_id=uuid4(),
        source_key="base",
        source_title="Base",
        content="content",
        chunk_index=0,
        score=1,
    )
    return KnowledgeRetrievalResult(
        chunks=tuple(replace(base, source_key=key) for key in keys),
        strategy=KnowledgeRetrievalStrategy.HYBRID_RRF,
    )


def test_evaluator_reports_relevance_rank_and_zero_leakage():
    cases = (
        RetrievalEvaluationCase(
            case_id="cancel",
            query="annuler",
            expected_source_keys=("cancel",),
            forbidden_source_keys=("internal",),
        ),
        RetrievalEvaluationCase(
            case_id="unknown", query="inconnu", expect_no_answer=True
        ),
    )
    results = {"cancel": result_for("other", "cancel"), "unknown": result_for()}
    report = RetrievalEvaluator().evaluate(
        cases, retrieve=lambda case: results[case.case_id]
    )

    assert report.metrics["hit_rate"] == 1
    assert report.metrics["mean_reciprocal_rank"] == 0.5
    assert report.metrics["forbidden_leakage_rate"] == 0
    assert report.metrics["correct_abstention_count"] == 1
    assert report.metrics["abstention_accuracy"] == 1


def test_evaluator_detects_forbidden_source_leakage():
    case = RetrievalEvaluationCase(
        case_id="leak",
        query="private",
        expected_source_keys=("public",),
        forbidden_source_keys=("other-tenant",),
    )
    report = RetrievalEvaluator().evaluate(
        (case,), retrieve=lambda _case: result_for("public", "other-tenant")
    )

    assert report.metrics["forbidden_leakage_rate"] == 1


def test_evaluator_rejects_ambiguous_case_definition():
    case = RetrievalEvaluationCase(
        case_id="ambiguous",
        query="question",
        expected_source_keys=("public",),
        expect_no_answer=True,
    )

    try:
        RetrievalEvaluator().evaluate((case,), retrieve=lambda _case: result_for())
    except ValueError as exc:
        assert "sources or an abstention" in str(exc)
    else:
        raise AssertionError("Ambiguous evaluation case was accepted.")
