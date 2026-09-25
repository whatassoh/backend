from uuid import uuid4

import pytest

from apps.ai.application.service import AIService
from apps.ai.domain.exceptions import AIInvalidResponseError
from apps.ai.providers.mock import MockAIProvider
from apps.knowledge.application.answers import KnowledgeAnswerService
from apps.knowledge.application.chunking import chunk_content
from apps.knowledge.domain.dtos import (
    KnowledgeChunkResult,
    KnowledgeNoAnswerReason,
    KnowledgeRetrievalResult,
    KnowledgeRetrievalStrategy,
)


class StaticRetriever:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def retrieve(self, query):
        self.calls += 1
        return self.result


def retrieval_for(content="Une annulation est possible avant confirmation."):
    source_id, chunk_id = uuid4(), uuid4()
    return KnowledgeRetrievalResult(
        chunks=(
            KnowledgeChunkResult(
                chunk_id=chunk_id,
                source_id=source_id,
                source_key="cancel",
                source_title="Annulation",
                source_version=2,
                canonical_url="https://help.example.test/annulation",
                content=content,
                chunk_index=0,
                score=1.0,
            ),
        ),
        strategy=KnowledgeRetrievalStrategy.POSTGRES_FULL_TEXT,
    )


def test_chunking_is_deterministic_and_bounded():
    content = "Premier paragraphe.\n\nSecond paragraphe."
    assert chunk_content(content) == ["Premier paragraphe.\n\nSecond paragraphe."]
    assert chunk_content("a" * 801) == ["a" * 800, "a"]


def test_chunking_keeps_text_directly_below_markdown_heading():
    from apps.knowledge.application.chunking import DocumentChunker

    chunks = DocumentChunker(
        target_characters=200,
        max_characters=240,
        overlap_characters=20,
    ).split("# Annulation\nVous pouvez annuler avant confirmation.")

    assert len(chunks) == 1
    assert chunks[0].section_path == "Annulation"
    assert chunks[0].content == "Vous pouvez annuler avant confirmation."


def test_chunking_never_exceeds_hard_limit_with_overlap():
    from apps.knowledge.application.chunking import DocumentChunker

    chunks = DocumentChunker(
        target_characters=200,
        max_characters=220,
        overlap_characters=40,
    ).split("A" * 500)

    assert chunks
    assert all(len(chunk.content) <= 220 for chunk in chunks)


def test_no_retrieved_knowledge_returns_controlled_no_answer():
    service = KnowledgeAnswerService(
        StaticRetriever(
            KnowledgeRetrievalResult(
                chunks=(),
                strategy=KnowledgeRetrievalStrategy.POSTGRES_FULL_TEXT,
                no_answer_reason=KnowledgeNoAnswerReason.LOW_RELEVANCE,
            )
        ),
        AIService(MockAIProvider()),
    )
    result = service.answer("Comment fonctionne une annulation ?")
    assert result.insufficient_knowledge is True
    assert result.grounded is False
    assert result.no_answer_reason == KnowledgeNoAnswerReason.LOW_RELEVANCE


def test_grounded_answer_has_server_derived_claim_citations():
    retrieval = retrieval_for()
    chunk = retrieval.chunks[0]
    provider = MockAIProvider()
    provider.register_response(
        task_name="grounded_knowledge_answer",
        result={
            "claims": [
                {
                    "text": "Vous pouvez annuler avant la confirmation.",
                    "cited_chunk_ids": [str(chunk.chunk_id)],
                    "evidence_quote": "annulation est possible avant confirmation",
                }
            ],
            "insufficient_knowledge": False,
            "confidence": 0.9,
        },
    )
    result = KnowledgeAnswerService(
        StaticRetriever(retrieval), AIService(provider)
    ).answer("Comment annuler ?")

    assert result.grounded is True
    assert result.chunk_ids == (chunk.chunk_id,)
    assert result.source_ids == (chunk.source_id,)
    assert result.citations[0].source_version == 2
    assert "[1]" in result.answer
    assert "https://help.example.test/annulation" in result.answer


def test_answer_cannot_cite_non_retrieved_knowledge():
    retrieval = retrieval_for()
    provider = MockAIProvider()
    provider.register_response(
        task_name="grounded_knowledge_answer",
        result={
            "claims": [
                {
                    "text": "Réponse",
                    "cited_chunk_ids": [str(uuid4())],
                    "evidence_quote": "annulation est possible",
                }
            ],
            "insufficient_knowledge": False,
            "confidence": 0.9,
        },
    )
    with pytest.raises(AIInvalidResponseError):
        KnowledgeAnswerService(StaticRetriever(retrieval), AIService(provider)).answer(
            "Comment annuler ?"
        )


def test_answer_rejects_quote_absent_from_cited_chunk():
    retrieval = retrieval_for()
    chunk = retrieval.chunks[0]
    provider = MockAIProvider()
    provider.register_response(
        task_name="grounded_knowledge_answer",
        result={
            "claims": [
                {
                    "text": "Réponse inventée",
                    "cited_chunk_ids": [str(chunk.chunk_id)],
                    "evidence_quote": "ce texte n'existe jamais dans la source",
                }
            ],
            "insufficient_knowledge": False,
            "confidence": 0.9,
        },
    )
    with pytest.raises(AIInvalidResponseError):
        KnowledgeAnswerService(StaticRetriever(retrieval), AIService(provider)).answer(
            "Comment annuler ?"
        )


def test_dynamic_business_query_never_reaches_static_retriever():
    retriever = StaticRetriever(retrieval_for())
    result = KnowledgeAnswerService(retriever, AIService(MockAIProvider())).answer(
        "Quel est le prix et le stock du riz aujourd'hui ?"
    )

    assert retriever.calls == 0
    assert result.no_answer_reason == KnowledgeNoAnswerReason.DYNAMIC_BUSINESS_DATA
