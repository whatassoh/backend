from types import SimpleNamespace
from uuid import uuid4

from django.test import override_settings

from apps.knowledge.application import cache as cache_module


@override_settings(AI_RAG_CANDIDATE_LIMIT=2)
def test_candidate_cache_accepts_only_bounded_unique_uuids(monkeypatch):
    valid_ids = [str(uuid4()), str(uuid4())]
    cached = {"value": {"schema": 1, "chunk_ids": valid_ids}}
    monkeypatch.setattr(
        cache_module,
        "cache",
        SimpleNamespace(get=lambda _key: cached["value"]),
    )
    assert cache_module.get_cached_candidate_ids("valid") == valid_ids

    invalid_values = (
        {"schema": 1, "chunk_ids": ["not-a-uuid"]},
        {"schema": 1, "chunk_ids": [valid_ids[0], valid_ids[0]]},
        {"schema": 1, "chunk_ids": [*valid_ids, str(uuid4())]},
    )
    for value in invalid_values:
        cached["value"] = value
        assert cache_module.get_cached_candidate_ids("invalid") is None
