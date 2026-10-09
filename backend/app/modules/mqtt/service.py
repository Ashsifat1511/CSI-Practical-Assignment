"""handle_mqtt_challenge(): one challenge in, one response out.

Uses exactly the same event service (process_batch) and state query (get_summary) as REST.
The challenge row, every event effect and the stored response commit in ONE transaction, so a crash
can never leave events processed without a stored response (or the other way round).
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from app.modules.events.service import SubmissionContext, process_batch
from app.modules.mqtt import protocol
from app.modules.mqtt import repository as repo
from app.modules.state.queries import get_summary
from app.shared import domain_events
from app.shared.db import transaction
from app.shared.timeutil import utcnow

log = logging.getLogger("mqtt.service")


def handle_mqtt_challenge(payload: Any, candidate_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utcnow()
    try:
        body = json.loads(payload) if isinstance(payload, (bytes, bytearray, str)) else payload
    except (ValueError, UnicodeDecodeError):
        return protocol.build_failed(candidate_id, None, protocol.VALIDATION_ERROR, "Payload is not valid JSON", now)

    challenge_id = protocol.challenge_id_of(body)
    if challenge_id is None:
        return protocol.build_failed(candidate_id, None, protocol.VALIDATION_ERROR,
                                     "Challenge must be a JSON object with a non-empty challenge_id", now)
    try:
        return _handle(body, challenge_id, candidate_id, now)
    except Exception:
        log.exception("challenge %s failed", challenge_id)
        return protocol.build_failed(candidate_id, challenge_id, protocol.INTERNAL_ERROR,
                                     "Internal error while processing challenge", now)


def _handle(body: Dict[str, Any], challenge_id: str, candidate_id: str, now: datetime) -> Dict[str, Any]:
    request_digest = protocol.digest(body)
    with transaction() as conn:
        if not repo.try_insert_challenge(conn, challenge_id, request_digest, body):
            existing = repo.get_challenge(conn, challenge_id)
            if existing["request_digest"] == request_digest and existing["response"] is not None:
                log.info("replay of %s: returning stored response without reprocessing", challenge_id)
                return existing["response"]
            return protocol.build_failed(candidate_id, challenge_id, protocol.CHALLENGE_CONFLICT,
                                         "challenge_id already used with a different body", now)

        try:
            events = protocol.validate_challenge(body, candidate_id, now)
        except protocol.ChallengeError as err:
            response = protocol.build_failed(candidate_id, challenge_id, err.code, err.message, now)
            repo.save_response(conn, challenge_id, response, "FAILED", err.code)
            return response

        results = process_batch(conn, events, SubmissionContext(channel="MQTT", challenge_id=challenge_id))
        state = get_summary(conn).to_dict()
        response = protocol.build_completed(
            candidate_id, challenge_id,
            [{"event_id": r.event_id, "status": r.status, "message": r.message} for r in results],
            state, now,
        )
        repo.save_response(conn, challenge_id, response, "COMPLETED", None)
        domain_events.queue(domain_events.CHALLENGE_COMPLETED, {"challenge_id": challenge_id, "events": len(events)})
        return response


def record_worker_status(**fields: Any) -> None:
    """Persist worker connectivity info for the dashboard; never let a DB hiccup kill the MQTT loop."""
    try:
        with transaction() as conn:
            repo.update_worker_status(conn, **fields)
    except Exception:
        log.exception("could not persist worker status")


def get_mqtt_overview(candidate_id: str, heartbeat_seconds: int) -> Dict[str, Any]:
    """Read model for the dashboard MQTT panel."""
    with transaction() as conn:
        status = repo.get_worker_status(conn)
        counts = repo.challenge_counts(conn)
        recent = repo.recent_challenges(conn)
    state = status["connection_state"]
    last_beat = status["last_heartbeat_at"]
    if state == "ONLINE" and (last_beat is None or (utcnow() - last_beat).total_seconds() > heartbeat_seconds * 3):
        state = "STALE"  # worker container stopped without its last will reaching us
    return {
        **status,
        "connection_state": state,
        "configured_candidate_id": candidate_id,
        "topics": protocol.topics(status["candidate_id"] or candidate_id),
        "challenge_counts": counts,
        "recent_challenges": recent,
    }
