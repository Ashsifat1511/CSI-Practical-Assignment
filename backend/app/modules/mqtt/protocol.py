"""MQTT protocol: topics, envelope validation and response builders. Pure functions, no I/O."""
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.shared.timeutil import parse_iso_with_tz, to_iso_utc

PROTOCOL_VERSION = "1.0"
COMMAND_PROCESS_EVENTS = "PROCESS_EVENTS"

VALIDATION_ERROR = "VALIDATION_ERROR"
CANDIDATE_MISMATCH = "CANDIDATE_MISMATCH"
UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL"
CHALLENGE_EXPIRED = "CHALLENGE_EXPIRED"
CHALLENGE_CONFLICT = "CHALLENGE_CONFLICT"
INTERNAL_ERROR = "INTERNAL_ERROR"


class ChallengeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def topics(candidate_id: str) -> Dict[str, str]:
    base = f"fse-01/{candidate_id}"
    return {"challenge": f"{base}/challenge", "response": f"{base}/response", "status": f"{base}/status"}


def digest(body: Dict[str, Any]) -> str:
    """Stable fingerprint of a challenge body (key order and whitespace do not matter)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def challenge_id_of(body: Any) -> Optional[str]:
    if isinstance(body, dict) and isinstance(body.get("challenge_id"), str) and body["challenge_id"].strip():
        return body["challenge_id"].strip()
    return None


def validate_challenge(body: Dict[str, Any], candidate_id: str, now: datetime) -> List[Any]:
    """Return the events list, or raise ChallengeError. Runs before any event is processed."""
    if body.get("protocol_version") != PROTOCOL_VERSION:
        raise ChallengeError(UNSUPPORTED_PROTOCOL, f"protocol_version must be {PROTOCOL_VERSION}")
    if body.get("candidate_id") != candidate_id:
        raise ChallengeError(CANDIDATE_MISMATCH, f"candidate_id does not match {candidate_id}")
    if body.get("command") != COMMAND_PROCESS_EVENTS:
        raise ChallengeError(VALIDATION_ERROR, f"command must be {COMMAND_PROCESS_EVENTS}")
    expires_at = parse_iso_with_tz(body.get("expires_at"))
    if expires_at is None:
        raise ChallengeError(VALIDATION_ERROR, "expires_at must be an ISO 8601 timestamp with timezone")
    if expires_at <= now:
        raise ChallengeError(CHALLENGE_EXPIRED, f"challenge expired at {to_iso_utc(expires_at)}")
    events = body.get("events")
    if not isinstance(events, list):
        raise ChallengeError(VALIDATION_ERROR, "events must be an array")
    return events


def build_completed(candidate_id: str, challenge_id: str, results: List[Dict[str, Any]],
                    state: Dict[str, int], now: datetime) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": candidate_id,
        "challenge_id": challenge_id,
        "status": "COMPLETED",
        "processed_at": to_iso_utc(now),
        "results": results,
        "state": state,
    }


def build_failed(candidate_id: str, challenge_id: Optional[str], code: str, message: str,
                 now: datetime) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": candidate_id,
        "challenge_id": challenge_id,
        "status": "FAILED",
        "processed_at": to_iso_utc(now),
        "error": {"code": code, "message": message},
    }


def build_status(candidate_id: str, client_id: str, status: str, now: datetime) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": candidate_id,
        "client_id": client_id,
        "status": status,
        "sent_at": to_iso_utc(now),
    }
