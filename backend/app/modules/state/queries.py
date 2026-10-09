"""State module: read-only queries. Totals always come from PostgreSQL, never from process memory.

``get_summary`` is shared by GET /api/state and the MQTT challenge response.
"""
from typing import Any, Dict, List, Optional

from psycopg import Connection

from app.shared.contracts import Summary


def get_summary(conn: Connection, source_id: Optional[str] = None) -> Summary:
    params = {"src": source_id}
    ev = conn.execute(
        """
        SELECT
          COALESCE(SUM(quantity) FILTER (WHERE type = 'COUNT' AND status = 'ACCEPTED'
                                         AND voided_by_event_id IS NULL), 0)        AS net_total,
          COUNT(*) FILTER (WHERE status = 'ACCEPTED')                               AS processed_events,
          COUNT(*) FILTER (WHERE status = 'ACCEPTED' AND acknowledged_at IS NULL)   AS pending_ack,
          COUNT(*) FILTER (WHERE status = 'PENDING_REFERENCE')                      AS unresolved
        FROM production_events
        WHERE (%(src)s::text IS NULL OR source_id = %(src)s)
        """,
        params,
    ).fetchone()
    at = conn.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE classification = 'DUPLICATE') AS duplicates,
          COUNT(*) FILTER (WHERE classification = 'CONFLICT')  AS conflicts,
          COUNT(*) FILTER (WHERE classification = 'REJECTED')  AS rejected_submissions
        FROM submission_attempts
        WHERE (%(src)s::text IS NULL OR source_id = %(src)s)
        """,
        params,
    ).fetchone()
    return Summary(
        net_total=int(ev["net_total"]),
        processed_events=ev["processed_events"],
        pending_ack=ev["pending_ack"],
        unresolved=ev["unresolved"],
        duplicates=at["duplicates"],
        conflicts=at["conflicts"],
        rejected_submissions=at["rejected_submissions"],
    )


def get_pending(conn: Connection, source_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """COUNT events processed successfully and waiting for supervisor acknowledgement.

    VOIDs are correction records and are auto-acknowledged, so they never appear here.
    """
    return conn.execute(
        """
        SELECT event_id, source_id, type, quantity, target_event_id, event_time, received_at,
               status, voided_by_event_id, channel
        FROM production_events
        WHERE status = 'ACCEPTED' AND acknowledged_at IS NULL AND type = 'COUNT'
          AND (%(src)s::text IS NULL OR source_id = %(src)s)
        ORDER BY seq
        """,
        {"src": source_id},
    ).fetchall()


def get_exceptions(conn: Connection, source_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """Unresolved references, rejected submissions and conflict attempts, newest first, with reasons."""
    return conn.execute(
        """
        SELECT * FROM (
            SELECT 'UNRESOLVED_REFERENCE' AS kind, event_id, source_id, type, quantity, target_event_id,
                   normalized->>'event_time' AS event_time, received_at, status, reason, channel, NULL::text AS challenge_id
            FROM production_events
            WHERE status = 'PENDING_REFERENCE' AND (%(src)s::text IS NULL OR source_id = %(src)s)
          UNION ALL
            SELECT 'REJECTED_VOID', event_id, source_id, type, quantity, target_event_id,
                   normalized->>'event_time', resolved_at, status, reason, channel, NULL
            FROM production_events
            WHERE status = 'REJECTED' AND (%(src)s::text IS NULL OR source_id = %(src)s)
          UNION ALL
            SELECT CASE classification WHEN 'CONFLICT' THEN 'CONFLICT' ELSE 'REJECTED' END,
                   event_id, source_id, raw_payload->>'type',
                   NULL, raw_payload->>'target_event_id', raw_payload->>'event_time',
                   received_at, classification, reason, channel, challenge_id
            FROM submission_attempts
            WHERE classification IN ('REJECTED', 'CONFLICT') AND (%(src)s::text IS NULL OR source_id = %(src)s)
        ) x
        ORDER BY received_at DESC
        LIMIT %(limit)s
        """,
        {"src": source_id, "limit": limit},
    ).fetchall()
