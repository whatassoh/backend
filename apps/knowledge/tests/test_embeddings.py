from types import SimpleNamespace

import pytest
from django.test import override_settings

from apps.knowledge.application.embeddings import OpenAIEmbeddingProvider
from apps.knowledge.domain.exceptions import KnowledgeEmbeddingError


class EmbeddingsClient:
    def __init__(self, data):
        self.embeddings = self
        self.data = data
        self.request = None

    def create(self, **request):
        self.request = request
        return SimpleNamespace(data=self.data)


@override_settings(
    AI_RAG_EMBEDDING_MODEL="test-model",
    AI_RAG_EMBEDDING_DIMENSIONS=2,
    AI_RAG_EMBEDDING_SEND_DIMENSIONS=True,
    AI_TIMEOUT_SECONDS=5,
)
def test_embedding_provider_restores_provider_index_order():
    client = EmbeddingsClient(
        [
            SimpleNamespace(index=1, embedding=[0.3, 0.4]),
            SimpleNamespace(index=0, embedding=[0.1, 0.2]),
        ]
    )
    provider = OpenAIEmbeddingProvider(client=client, api_key="test")

    vectors = provider.embed(["first", "second"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert client.request["dimensions"] == 2


@override_settings(
    AI_RAG_EMBEDDING_MODEL="test-model",
    AI_RAG_EMBEDDING_DIMENSIONS=2,
    AI_RAG_EMBEDDING_SEND_DIMENSIONS=True,
    AI_TIMEOUT_SECONDS=5,
)
def test_embedding_provider_rejects_duplicate_result_indexes():
    client = EmbeddingsClient(
        [
            SimpleNamespace(index=0, embedding=[0.1, 0.2]),
            SimpleNamespace(index=0, embedding=[0.3, 0.4]),
        ]
    )
    provider = OpenAIEmbeddingProvider(client=client, api_key="test")

    with pytest.raises(KnowledgeEmbeddingError, match="indexes"):
        provider.embed(["first", "second"])
