"""DB-deadline scheduling helpers; ETA is an optimisation, never truth."""

from common.async_tasks import enqueue_after_commit


def schedule_deadline(*, task, entity_id, expires_at, task_name, correlation_id=None):
    """Publish a JSON-only ETA after commit; a sweep recovers publication loss."""
    enqueue_after_commit(
        lambda: task.apply_async(args=[str(entity_id), correlation_id], eta=expires_at),
        task_name=task_name,
        entity_id=str(entity_id),
        correlation_id=correlation_id,
    )
