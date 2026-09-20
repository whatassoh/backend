"""Small, DB-first conventions shared by Celery entry points.

Celery is an at-least-once transport in this project.  Tasks therefore only
receive JSON-safe identifiers, reload state from PostgreSQL, and let an
application service make the idempotent business decision.
"""

import logging
from collections.abc import Callable
from typing import Any

import structlog
from celery import Task
from django.db import transaction

logger = logging.getLogger(__name__)


class RetryableTechnicalError(Exception):
    """An infrastructure failure which a task policy may retry later."""


class NonRetryableTaskError(Exception):
    """A known permanent failure; never use this for domain state changes."""


class WhatassohTask(Task):
    """Restores safe correlation context and emits lifecycle logs.

    It deliberately does not auto-retry arbitrary exceptions.  Individual
    tasks must classify technical provider failures before scheduling retry.
    """

    abstract = True

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        correlation_id = kwargs.get("correlation_id")
        if correlation_id is None and len(args) > 1:
            correlation_id = args[1]
        structlog.contextvars.bind_contextvars(
            task_name=self.name,
            task_id=self.request.id,
            correlation_id=correlation_id,
        )
        logger.info(
            "celery_task_started",
            extra={"task_name": self.name, "task_id": self.request.id,
                   "correlation_id": correlation_id, "attempt": self.request.retries},
        )
        try:
            result = super().__call__(*args, **kwargs)
        except Exception:
            logger.exception(
                "celery_task_failed",
                extra={"task_name": self.name, "task_id": self.request.id,
                       "correlation_id": correlation_id, "attempt": self.request.retries},
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars(
                "task_name", "task_id", "correlation_id"
            )
        logger.info(
            "celery_task_succeeded",
            extra={"task_name": self.name, "task_id": self.request.id,
                   "correlation_id": correlation_id, "attempt": self.request.retries},
        )
        return result


def enqueue_after_commit(enqueue: Callable[[], Any], *, task_name: str, entity_id: str,
                         correlation_id: str | None = None) -> None:
    """Run enqueue only after commit; broker failure never rewrites DB truth."""

    def callback() -> None:
        try:
            enqueue()
        except Exception:
            logger.exception(
                "celery_enqueue_failed",
                extra={"task_name": task_name, "entity_id": entity_id,
                       "correlation_id": correlation_id, "classification": "TECHNICAL"},
            )

    transaction.on_commit(callback)
