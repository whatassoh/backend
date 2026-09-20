from unittest.mock import Mock

import pytest
from django.db import transaction
from django.utils import timezone

from common.async_tasks import enqueue_after_commit
from common.deadlines import schedule_deadline


def test_celery_discovers_stable_json_tasks():
    from config.celery import app

    app.loader.import_default_modules()
    names = {
        "stores.deliver_store_offer_notification",
        "stores.recover_store_offer_notification",
        "stores.reconcile_store_matching",
        "drivers.deliver_driver_offer_notification",
        "drivers.expire_driver_offer",
        "drivers.reconcile_driver_matching",
        "messaging.deliver_operational_notification",
    }
    assert names <= set(app.tasks)
    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert app.conf.accept_content == ["json"]


@pytest.mark.django_db(transaction=True)
def test_enqueue_after_commit_runs_only_after_commit():
    enqueue = Mock()

    with transaction.atomic():
        enqueue_after_commit(enqueue, task_name="test.task", entity_id="opaque-id")
        assert not enqueue.called

    enqueue.assert_called_once_with()


@pytest.mark.django_db(transaction=True)
def test_enqueue_after_commit_does_not_run_after_rollback():
    enqueue = Mock()

    with pytest.raises(RuntimeError), transaction.atomic():
        enqueue_after_commit(enqueue, task_name="test.task", entity_id="opaque-id")
        raise RuntimeError("rollback")

    enqueue.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_deadline_eta_is_published_only_after_commit():
    task = Mock()
    with transaction.atomic():
        schedule_deadline(task=task, entity_id="opaque-id", expires_at=timezone.now(), task_name="test.deadline")
        task.apply_async.assert_not_called()
    task.apply_async.assert_called_once()
