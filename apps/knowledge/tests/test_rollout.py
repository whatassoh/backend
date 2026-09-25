from uuid import uuid4

from django.test import override_settings

from apps.knowledge.application.rollout import rag_enabled_for


@override_settings(AI_RAG_ROLLOUT_PERCENT=0)
def test_rollout_zero_is_explicit():
    assert rag_enabled_for(uuid4()) is False


@override_settings(AI_RAG_ROLLOUT_PERCENT=100)
def test_rollout_full_is_explicit():
    assert rag_enabled_for(uuid4()) is True


@override_settings(AI_RAG_ROLLOUT_PERCENT=37)
def test_partial_rollout_assignment_is_stable():
    user_id = uuid4()
    assert rag_enabled_for(user_id) == rag_enabled_for(user_id)
