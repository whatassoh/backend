from uuid import uuid4

import pytest

from apps.ai.application.service import AIService
from apps.ai.domain.exceptions import AIInvalidResponseError
from apps.ai.providers.mock import MockAIProvider
from apps.knowledge.application.services import KnowledgeAnswerService, chunk_content
from apps.knowledge.domain.dtos import (
    KnowledgeChunkResult,
    KnowledgeRetrievalResult,
    KnowledgeRetrievalStrategy,
)


class StaticRetriever:
    def __init__(self, result):
        self.result = result

    def retrieve(self, query, *, language=None, limit=5):
        return self.result


def test_chunking_is_deterministic_and_keeps_paragraphs():
    content = "Premier paragraphe.\n\nSecond paragraphe."
    assert chunk_content(content) == [content]
    assert chunk_content("a" * 801) == ["a" * 800, "a"]


def test_no_retrieved_knowledge_returns_controlled_no_answer():
    service = KnowledgeAnswerService(
        StaticRetriever(
            KnowledgeRetrievalResult(
                chunks=(), strategy=KnowledgeRetrievalStrategy.POSTGRES_FULL_TEXT
            )
        ),
        AIService(MockAIProvider()),
    )
    result = service.answer("Livrez-vous à Garoua ?")
    assert result.insufficient_knowledge is True
    assert result.grounded is False
    assert result.source_ids == ()


def test_grounded_answer_keeps_server_derived_lineage():
    source_id, chunk_id = uuid4(), uuid4()
    retrieval = KnowledgeRetrievalResult(
        chunks=(
            KnowledgeChunkResult(
                chunk_id=chunk_id,
                source_id=source_id,
                source_key="cancel",
                source_title="Annulation",
                content="Une annulation suit les informations validées.",
                chunk_index=0,
                score=1.0,
            ),
        ),
        strategy=KnowledgeRetrievalStrategy.POSTGRES_FULL_TEXT,
    )
    provider = MockAIProvider()
    provider.register_response(
        task_name="grounded_knowledge_answer",
        result={
            "answer": "Consultez les informations d'annulation validées.",
            "grounded": True,
            "insufficient_knowledge": False,
            "cited_chunk_ids": [str(chunk_id)],
            "confidence": 0.9,
        },
    )
    result = KnowledgeAnswerService(
        StaticRetriever(retrieval), AIService(provider)
    ).answer("Comment annuler ?")
    assert result.grounded is True
    assert result.chunk_ids == (chunk_id,)
    assert result.source_ids == (source_id,)


def test_answer_cannot_cite_non_retrieved_knowledge():
    chunk_id = uuid4()
    retrieval = KnowledgeRetrievalResult(
        chunks=(
            KnowledgeChunkResult(
                chunk_id=chunk_id,
                source_id=uuid4(),
                source_key="delivery",
                source_title="Livraison",
                content="Contenu",
                chunk_index=0,
                score=1.0,
            ),
        ),
        strategy=KnowledgeRetrievalStrategy.POSTGRES_FULL_TEXT,
    )
    provider = MockAIProvider()
    provider.register_response(
        task_name="grounded_knowledge_answer",
        result={
            "answer": "Réponse",
            "grounded": True,
            "insufficient_knowledge": False,
            "cited_chunk_ids": [str(uuid4())],
            "confidence": 0.9,
        },
    )
    with pytest.raises(AIInvalidResponseError):
        KnowledgeAnswerService(StaticRetriever(retrieval), AIService(provider)).answer(
            "Question"
        )
