"""Data access for production events, sources and submission attempts. SQL only, no business rules."""
from typing import Any, Dict, List, Optional

from psycopg import Connection
from psycopg.types.json import Jsonb


def lock_event_key(conn: Connection, key: str) -> None:
    """Transaction-scoped advisory lock that serialises work on one COUNT and its VOIDs."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))


def ensure_source(conn: Connection, source_id: str) -> None:
    conn.execute(
        "INSERT INTO production_sources (source_id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (source_id, source_id),
    )


def get_event(conn: Connection, event_id: str, for_update: bool = False) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM production_events WHERE event_id = %s"
    if for_update:
        sql += " FOR UPDATE"
    return conn.execute(sql, (event_id,)).fetchone()


def insert_event(
    conn: Connection,
    *,
    normalized: Dict[str, Any],
    raw_payload: Any,
    status: str,
    channel: str,
    auto_ack: bool = False,
    resolved: bool = False,
    reason: Optional[str] = None,
) -> bool:
    """Insert a logical event. Returns False if the event_id already exists (no row written)."""
    row = conn.execute(
        """
        INSERT INTO production_events
            (event_id, source_id, type, quantity, target_event_id, event_time, status, reason,
             normalized, raw_payload, channel, resolved_at, acknowledged_at, acknowledged_by)
        VALUES (%(event_id)s, %(source_id)s, %(type)s, %(quantity)s, %(target_event_id)s, %(event_time)s,
                %(status)s, %(reason)s, %(normalized)s, %(raw)s, %(channel)s,
                CASE WHEN %(resolved)s THEN now() END,
                CASE WHEN %(auto_ack)s THEN now() END,
                CASE WHEN %(auto_ack)s THEN 'SYSTEM:auto-correction' END)
        ON CONFLICT DO NOTHING
        RETURNING event_id
        """,
        {
            **normalized,
            "status": status,
            "reason": reason,
            "normalized": Jsonb(normalized),
            "raw": Jsonb(raw_payload),
            "channel": channel,
            "resolved": resolved,
            "auto_ack": auto_ack,
        },
    ).fetchone()
    return row is not None


def set_voided_by(conn: Connection, count_event_id: str, void_event_id: str) -> None:
    conn.execute(
        "UPDATE production_events SET voided_by_event_id = %s WHERE event_id = %s AND voided_by_event_id IS NULL",
        (void_event_id, count_event_id),
    )


def pending_voids_for(conn: Connection, count_event_id: str) -> List[Dict[str, Any]]:
    return conn.execute(
        """
        SELECT * FROM production_events
        WHERE target_event_id = %s AND type = 'VOID' AND status = 'PENDING_REFERENCE'
        ORDER BY seq
        FOR UPDATE
        """,
        (count_event_id,),
    ).fetchall()


def accept_pending_void(conn: Connection, void_event_id: str) -> None:
    conn.execute(
        """
        UPDATE production_events
        SET status = 'ACCEPTED', resolved_at = now(), reason = NULL,
            acknowledged_at = now(), acknowledged_by = 'SYSTEM:auto-correction'
        WHERE event_id = %s
        """,
        (void_event_id,),
    )


def reject_pending_void(conn: Connection, void_event_id: str, reason: str) -> None:
    conn.execute(
        "UPDATE production_events SET status = 'REJECTED', resolved_at = now(), reason = %s WHERE event_id = %s",
        (reason, void_event_id),
    )


def insert_attempt(
    conn: Connection,
    *,
    channel: str,
    challenge_id: Optional[str],
    batch_index: Optional[int],
    raw_payload: Any,
    source_id: Optional[str],
    event_id: Optional[str],
    classification: str,
    reason: Optional[str],
) -> None:
    conn.execute(
        """
        INSERT INTO submission_attempts
            (channel, challenge_id, batch_index, raw_payload, source_id, event_id, classification, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (channel, challenge_id, batch_index, Jsonb(raw_payload), source_id, event_id, classification, reason),
    )
