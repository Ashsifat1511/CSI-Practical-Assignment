from typing import Any, Dict, List, Optional

from psycopg import Connection
from psycopg.types.json import Jsonb


def insert_entry(conn: Connection, event_type: str, subject_id: Optional[str], payload: Dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO audit_log (event_type, subject_id, payload) VALUES (%s, %s, %s)",
        (event_type, subject_id, Jsonb(payload)),
    )


def recent(conn: Connection, limit: int = 50) -> List[Dict[str, Any]]:
    return conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
