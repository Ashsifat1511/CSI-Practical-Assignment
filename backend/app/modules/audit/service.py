"""Audit module: listens to domain notifications (dispatched only after commit) and keeps a trace log."""
import logging
from typing import Any, Dict

from app.modules.audit import repository as repo
from app.shared import domain_events
from app.shared.db import get_pool

log = logging.getLogger("audit")

_registered = False


def record(name: str, payload: Dict[str, Any]) -> None:
    log.info("%s %s", name, payload)
    with get_pool().connection() as conn:
        repo.insert_entry(conn, name, payload.get("event_id") or payload.get("challenge_id"), payload)


def register_audit_handlers() -> None:
    global _registered
    if _registered:
        return
    for name in (domain_events.EVENT_ACCEPTED, domain_events.EVENT_PENDING, domain_events.EVENT_REJECTED,
                 domain_events.VOID_RESOLVED, domain_events.EVENT_ACKNOWLEDGED, domain_events.CHALLENGE_COMPLETED):
        domain_events.subscribe(name, record)
    _registered = True
