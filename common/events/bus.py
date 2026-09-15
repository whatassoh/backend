import logging
from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager

from common.events.domain import DomainEvent

EventHandler = Callable[[DomainEvent], None]

logger = logging.getLogger(__name__)
_EVENT_HANDLERS: dict[type[DomainEvent], list[EventHandler]] = defaultdict(list)


def subscribe(event_type: type[DomainEvent], handler: EventHandler) -> None:
    if handler not in _EVENT_HANDLERS[event_type]:
        _EVENT_HANDLERS[event_type].append(handler)


def unsubscribe(event_type: type[DomainEvent], handler: EventHandler) -> None:
    if handler in _EVENT_HANDLERS[event_type]:
        _EVENT_HANDLERS[event_type].remove(handler)


def publish(event: DomainEvent) -> None:
    for handler in list(_EVENT_HANDLERS[type(event)]):
        try:
            handler(event)
        except Exception:
            logger.exception(
                "Domain event handler failed.",
                extra={
                    "event_type": type(event).__name__,
                    "event_id": str(event.event_id),
                    "handler": getattr(handler, "__name__", repr(handler)),
                },
            )
            raise


def clear_subscribers() -> None:
    _EVENT_HANDLERS.clear()


@contextmanager
def subscribed(event_type: type[DomainEvent], handler: EventHandler):
    subscribe(event_type, handler)
    try:
        yield
    finally:
        unsubscribe(event_type, handler)
