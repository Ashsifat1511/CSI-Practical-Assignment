import json
from datetime import datetime, timedelta, timezone

from app.modules.mqtt.service import handle_mqtt_challenge
from app.shared.db import get_pool
from tests.conftest import count, void

CID = "CAND-TEST"


def challenge(challenge_id="CH-1", events=None, expires_in=15, **overrides):
    now = datetime.now(timezone.utc)
    body = {
        "protocol_version": "1.0",
        "candidate_id": CID,
        "challenge_id": challenge_id,
        "command": "PROCESS_EVENTS",
        "sent_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "events": events if events is not None else [count("EV-101", 5)],
    }
    body.update(overrides)
    return json.dumps(body)


def attempts():
    with get_pool().connection() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM submission_attempts").fetchone()["n"]


def test_repeated_challenge_is_not_processed_again():
    payload = challenge(events=[count("EV-101", 5), void("EV-102", "EV-101"), count("EV-103", 2)])
    first = handle_mqtt_challenge(payload, CID)
    assert first["status"] == "COMPLETED"
    assert [r["status"] for r in first["results"]] == ["ACCEPTED", "ACCEPTED", "ACCEPTED"]
    assert first["state"] == {"net_total": 2, "processed_events": 3, "pending_ack": 2,
                              "unresolved": 0, "duplicates": 0, "conflicts": 0, "rejected_submissions": 0}
    n = attempts()

    second = handle_mqtt_challenge(payload, CID)
    assert second == first  # original response returned verbatim
    assert attempts() == n  # no new submission attempts -> nothing reprocessed


def test_changed_body_same_id_is_challenge_conflict():
    handle_mqtt_challenge(challenge(), CID)
    resp = handle_mqtt_challenge(challenge(events=[count("EV-999", 1)]), CID)
    assert resp["status"] == "FAILED"
    assert resp["error"]["code"] == "CHALLENGE_CONFLICT"


def test_mqtt_and_rest_share_state(client):
    client.post("/api/events", json=count("EV-101", 5))
    resp = handle_mqtt_challenge(challenge(events=[count("EV-101", 5), count("EV-200", 1)]), CID)
    assert [r["status"] for r in resp["results"]] == ["DUPLICATE", "ACCEPTED"]
    assert resp["state"]["net_total"] == 6 and resp["state"]["duplicates"] == 1
    assert client.get("/api/state").json()["net_total"] == 6


def test_invalid_item_still_completes():
    resp = handle_mqtt_challenge(challenge(events=[{"event_id": "X"}, count("EV-1", 3)]), CID)
    assert resp["status"] == "COMPLETED"
    assert [r["status"] for r in resp["results"]] == ["REJECTED", "ACCEPTED"]


def test_mqtt_applies_quantity_limit_and_reports_rejected_submissions():
    resp = handle_mqtt_challenge(challenge(events=[count("EV-1", 450), count("EV-2", 501)]), CID)
    assert resp["status"] == "COMPLETED"
    assert [r["status"] for r in resp["results"]] == ["ACCEPTED", "REJECTED"]
    assert "exceeds the maximum of 500" in resp["results"][1]["message"]
    assert resp["state"]["net_total"] == 450
    assert resp["state"]["rejected_submissions"] == 1


def test_envelope_failures_do_not_process_events():
    cases = {
        "CHALLENGE_EXPIRED": challenge("CH-exp", expires_in=-1),
        "CANDIDATE_MISMATCH": challenge("CH-cand", candidate_id="CAND-OTHER"),
        "UNSUPPORTED_PROTOCOL": challenge("CH-proto", protocol_version="2.0"),
        "VALIDATION_ERROR": challenge("CH-cmd", command="DELETE_ALL"),
    }
    for code, payload in cases.items():
        resp = handle_mqtt_challenge(payload, CID)
        assert resp["status"] == "FAILED" and resp["error"]["code"] == code, code
    assert handle_mqtt_challenge(b"not json", CID)["error"]["code"] == "VALIDATION_ERROR"
    assert attempts() == 0
