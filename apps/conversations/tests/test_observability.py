from types import SimpleNamespace
from unittest.mock import Mock

from django.test import override_settings

from apps.ai.domain.exceptions import AITimeoutError
from apps.conversations.application import observability


@override_settings(AI_DECISION_OBSERVABILITY_ENABLED=True)
def test_ai_failure_metric_exposes_category_but_not_error_message(monkeypatch):
    labels = Mock()
    labels.inc = Mock()
    metric = Mock()
    metric.labels.return_value = labels
    monkeypatch.setattr(observability, "AI_FAILURES", metric)

    observability.observe_ai_failure(
        channel="WHATSAPP",
        error=AITimeoutError("secret provider payload"),
    )

    metric.labels.assert_called_once_with(
        channel="whatsapp",
        category="AITimeoutError",
        retryable="true",
    )
    labels.inc.assert_called_once_with()
    assert "secret provider payload" not in repr(metric.mock_calls)


@override_settings(AI_DECISION_OBSERVABILITY_ENABLED=False)
def test_disabled_observability_does_not_touch_metrics(monkeypatch):
    metric = Mock()
    monkeypatch.setattr(observability, "AI_FAILURES", metric)

    observability.observe_ai_failure(
        channel="WHATSAPP",
        error=AITimeoutError("timeout"),
    )

    metric.labels.assert_not_called()


def test_route_labels_are_bounded_to_known_metadata_keys():
    assert (
        observability._route_for(
            SimpleNamespace(status="PROCESSED", metadata={"capability": "greeting"})
        )
        == "capability"
    )
    assert (
        observability._route_for(
            SimpleNamespace(status="PROCESSED", metadata={"user_input": "anything"})
        )
        == "workflow"
    )
