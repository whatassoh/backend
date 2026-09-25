import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.application.embeddings import get_embedding_provider
from apps.knowledge.application.evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluator,
)
from apps.knowledge.application.retrieval import HybridKnowledgeRetriever
from apps.knowledge.domain.dtos import KnowledgePrincipal, KnowledgeQuery


class Command(BaseCommand):
    help = "Evaluate retrieval relevance and forbidden-source leakage from JSON."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--min-hit-rate", type=float, default=0.8)
        parser.add_argument("--min-mrr", type=float, default=0.7)

    def handle(self, *args, **options):
        try:
            payload = json.loads(Path(options["file"]).read_text(encoding="utf-8"))
            cases = tuple(
                RetrievalEvaluationCase(
                    case_id=str(item["case_id"]),
                    query=str(item["query"]),
                    expected_source_keys=tuple(item.get("expected_source_keys", ())),
                    forbidden_source_keys=tuple(item.get("forbidden_source_keys", ())),
                    expect_no_answer=bool(item.get("expect_no_answer", False)),
                    language=str(item.get("language", "fr")),
                    namespace=str(item.get("namespace", "customer-help")),
                )
                for item in payload
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CommandError(f"Invalid evaluation dataset: {exc}") from exc
        if not cases:
            raise CommandError("Evaluation dataset cannot be empty.")
        embedding_provider = (
            get_embedding_provider() if settings.AI_RAG_SEMANTIC_ENABLED else None
        )
        retriever = HybridKnowledgeRetriever(embedding_provider=embedding_provider)

        def retrieve(case):
            return retriever.retrieve(
                KnowledgeQuery(
                    text=case.query,
                    principal=KnowledgePrincipal(),
                    language=case.language,
                    namespace=case.namespace,
                    limit=settings.AI_RAG_MAX_RESULTS,
                    correlation_id=f"evaluation:{case.case_id}"[:128],
                )
            )

        try:
            report = RetrievalEvaluator().evaluate(cases, retrieve=retrieve)
        except ValueError as exc:
            raise CommandError(f"Invalid evaluation dataset: {exc}") from exc
        self.stdout.write(json.dumps(report.metrics, indent=2, sort_keys=True))
        failures = [
            detail
            for detail in report.cases
            if not detail["hit"] or detail["forbidden_leak"]
        ]
        for failure in failures:
            self.stdout.write(self.style.WARNING(json.dumps(failure, sort_keys=True)))
        metrics = report.metrics
        if (
            metrics["hit_rate"] < options["min_hit_rate"]
            or metrics["mean_reciprocal_rank"] < options["min_mrr"]
            or metrics["forbidden_leakage_rate"] > 0
        ):
            raise CommandError("Knowledge retrieval evaluation gate failed.")
        self.stdout.write(self.style.SUCCESS("Knowledge retrieval gate passed."))
