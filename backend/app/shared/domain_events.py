"""Tiny in-process notification bus.

Publishers call ``queue(name, payload)`` while inside a DB transaction. Notifications are held per
transaction and dispatched by ``flush`` only after a successful commit, so subscribers never see work
that was rolled back. Later this module can be replaced by a real message broker publisher.
"""
import logging
import threading
from typing import Any, Callable, Dict, List, Tuple

log = logging.getLogger(__name__)

EVENT_ACCEPTED = "EVENT_ACCEPTED"
EVENT_PENDING = "EVENT_PENDING_REFERENCE"
EVENT_REJECTED = "EVENT_REJECTED"
VOID_RESOLVED = "VOID_RESOLVED"
EVENT_ACKNOWLEDGED = "EVENT_ACKNOWLEDGED"
CHALLENGE_COMPLETED = "CHALLENGE_COMPLETED"

Notification = Tuple[str, Dict[str, Any]]
Handler = Callable[[str, Dict[str, Any]], None]

_subscribers: Dict[str, List[Handler]] = {}
_local = threading.local()


def subscribe(name: str, handler: Handler) -> None:
    _subscribers.setdefault(name, []).append(handler)


def _stack() -> List[List[Notification]]:
    if not hasattr(_local, "stack"):
        _local.stack = []
    return _local.stack


def begin() -> List[Notification]:
    buf: List[Notification] = []
    _stack().append(buf)
    return buf


def queue(name: str, payload: Dict[str, Any]) -> None:
    stack = _stack()
    if stack:
        stack[-1].append((name, payload))
    else:
        _dispatch(name, payload)


def discard(token: List[Notification]) -> None:
    stack = _stack()
    if token in stack:
        stack.remove(token)


def flush(token: List[Notification]) -> None:
    discard(token)
    for name, payload in token:
        _dispatch(name, payload)


def _dispatch(name: str, payload: Dict[str, Any]) -> None:
    for handler in _subscribers.get(name, []):
        try:
            handler(name, payload)
        except Exception:  # a failing subscriber must never break the producer
            log.exception("domain event handler failed for %s", name)
