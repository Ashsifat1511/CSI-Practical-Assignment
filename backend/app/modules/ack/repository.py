from typing import Any, Dict, Optional

from psycopg import Connection


def mark_acknowledged(conn: Connection, event_id: str, by: Optional[str]) -> bool:
    """Conditional update: only one caller can ever flip acknowledged_at from NULL (safe under concurrency)."""
    row = conn.execute(
        """
        UPDATE production_events
        SET acknowledged_at = now(), acknowledged_by = %s
        WHERE event_id = %s AND status = 'ACCEPTED' AND acknowledged_at IS NULL
        RETURNING event_id
        """,
        (by, event_id),
    ).fetchone()
    return row is not None


def get_ack_state(conn: Connection, event_id: str) -> Optional[Dict[str, Any]]:
    return conn.execute(
        "SELECT event_id, status, acknowledged_at FROM production_events WHERE event_id = %s", (event_id,)
    ).fetchone()
