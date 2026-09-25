class KnowledgeError(Exception):
    """Base exception for controlled knowledge-pipeline failures."""


class KnowledgeValidationError(KnowledgeError):
    pass


class KnowledgeIngestionRejected(KnowledgeValidationError):
    pass


class KnowledgeEmbeddingError(KnowledgeError):
    pass
