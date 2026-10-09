"""Data access for MQTT challenges and the worker status row."""
from typing import Any, Dict, List, Optional

from psycopg import Connection
from psycopg.types.json import Jsonb

STATUS_FIELDS = {
    "candidate_id", "client_id", "connection_state", "last_connected_at", "last_heartbeat_at",
    "last_challenge_id", "last_challenge_at", "last_response_status", "last_error", "last_error_at",
}


def try_insert_challenge(conn: Connection, challenge_id: str, request_digest: str, body: Dict[str, Any]) -> bool:
    """Claim a challenge id. A concurrent copy waits here until the first transaction finishes."""
    row = conn.execute(
        """
        INSERT INTO mqtt_challenges (challenge_id, request_digest, request_body, status)
        VALUES (%s, %s, %s, 'PROCESSING')
        ON CONFLICT (challenge_id) DO NOTHING
        RETURNING challenge_id
        """,
        (challenge_id, request_digest, Jsonb(body)),
    ).fetchone()
    return row is not None


def get_challenge(conn: Connection, challenge_id: str) -> Optional[Dict[str, Any]]:
    return conn.execute("SELECT * FROM mqtt_challenges WHERE challenge_id = %s", (challenge_id,)).fetchone()


def save_response(conn: Connection, challenge_id: str, response: Dict[str, Any], status: str,
                  error_code: Optional[str]) -> None:
    conn.execute(
        """
        UPDATE mqtt_challenges SET response = %s, status = %s, error_code = %s, responded_at = now()
        WHERE challenge_id = %s
        """,
        (Jsonb(response), status, error_code, challenge_id),
    )


def update_worker_status(conn: Connection, **fields: Any) -> None:
    fields = {k: v for k, v in fields.items() if k in STATUS_FIELDS}
    if not fields:
        return
    assignments = ", ".join(f"{k} = %({k})s" for k in fields)
    conn.execute(f"UPDATE mqtt_worker_status SET {assignments}, updated_at = now() WHERE id = 1", fields)


def get_worker_status(conn: Connection) -> Dict[str, Any]:
    return conn.execute("SELECT * FROM mqtt_worker_status WHERE id = 1").fetchone()


def challenge_counts(conn: Connection) -> Dict[str, int]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'COMPLETED') AS completed,
               COUNT(*) FILTER (WHERE status = 'FAILED')    AS failed
        FROM mqtt_challenges
        """
    ).fetchone()
    return dict(row)


def recent_challenges(conn: Connection, limit: int = 10) -> List[Dict[str, Any]]:
    return conn.execute(
        """
        SELECT challenge_id, status, error_code, received_at, responded_at,
               jsonb_array_length(CASE WHEN jsonb_typeof(request_body->'events') = 'array'
                                       THEN request_body->'events' ELSE '[]'::jsonb END) AS event_count,
               response->'state' AS state
        FROM mqtt_challenges ORDER BY received_at DESC LIMIT %s
        """,
        (limit,),
    ).fetchall()
