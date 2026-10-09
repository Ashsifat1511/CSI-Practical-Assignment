"""Acknowledgement module: supervisor review markers. Never deletes history and never blocks a later VOID."""
from typing import Any, Dict, List, Optional

from psycopg import Connection

from app.modules.ack import repository as repo
from app.shared import domain_events
from app.shared.contracts import AckStatus


def acknowledge_events(conn: Connection, event_ids: List[Any], by: Optional[str] = None) -> List[Dict[str, str]]:
    """One result per requested id, in request order. A repeated id in the same request sees the earlier ACK."""
    return [{"event_id": eid, "status": acknowledge_event(conn, eid, by)} for eid in event_ids]


def acknowledge_event(conn: Connection, event_id: Any, by: Optional[str] = None) -> str:
    if not isinstance(event_id, str) or not event_id.strip():
        return AckStatus.NOT_FOUND
    event_id = event_id.strip()

    if repo.mark_acknowledged(conn, event_id, by):
        domain_events.queue(domain_events.EVENT_ACKNOWLEDGED, {"event_id": event_id, "by": by})
        return AckStatus.ACKED

    row = repo.get_ack_state(conn, event_id)
    if row is None:
        return AckStatus.NOT_FOUND
    if row["status"] != "ACCEPTED":
        return AckStatus.NOT_READY
    return AckStatus.ALREADY_ACKED
