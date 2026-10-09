"""Change request 01/02: COUNT quantity limit (1..500) and rejected_submissions in the summary."""
from app.shared.db import get_pool
from tests.conftest import count, void


def summary(client, source=None):
    url = "/api/state?view=summary" + (f"&source_id={source}" if source else "")
    return client.get(url).json()


def statuses(resp):
    return [r["status"] for r in resp.json()["results"]]


def test_count_within_limit_accepted_and_over_limit_rejected(client):
    resp = client.post("/api/events", json=[count("EV-450", 450), count("EV-501", 501)])
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["status"] for r in results] == ["ACCEPTED", "REJECTED"]
    assert results[1]["event_id"] == "EV-501"
    assert "exceeds the maximum of 500" in results[1]["message"]

    s = summary(client)
    assert s["net_total"] == 450  # rejected COUNT never increases the total
    assert s["processed_events"] == 1
    assert s["rejected_submissions"] == 1


def test_boundaries(client):
    resp = client.post("/api/events", json=[count("EV-1", 1), count("EV-500", 500), count("EV-0", 0)])
    assert statuses(resp) == ["ACCEPTED", "ACCEPTED", "REJECTED"]
    assert summary(client)["net_total"] == 501


def test_rejected_submission_is_persisted_with_reason(client):
    client.post("/api/events", json=count("EV-BIG", 900))
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT classification, reason, source_id FROM submission_attempts WHERE event_id = 'EV-BIG'"
        ).fetchone()
        stored = conn.execute("SELECT 1 FROM production_events WHERE event_id = 'EV-BIG'").fetchone()
    assert row["classification"] == "REJECTED"
    assert "maximum of 500" in row["reason"]
    assert row["source_id"] == "LINE-01"
    assert stored is None  # never becomes a logical production event
    exceptions = client.get("/api/state?view=exceptions").json()["items"]
    assert any(e["event_id"] == "EV-BIG" and e["kind"] == "REJECTED" for e in exceptions)


def test_rejected_submissions_zero_when_none(client):
    s = summary(client)
    assert s["rejected_submissions"] == 0
    # the six original fields are still present and unchanged in meaning
    for key in ("net_total", "processed_events", "pending_ack", "unresolved", "duplicates", "conflicts"):
        assert s[key] == 0


def test_rejected_submissions_excludes_other_classifications(client):
    client.post("/api/events", json=[
        count("EV-1", 5),
        count("EV-1", 5),                  # DUPLICATE
        count("EV-1", 6),                  # CONFLICT
        void("EV-9", "EV-MISSING"),        # PENDING_REFERENCE
        count("EV-2", 700),                # REJECTED (over limit)
        {"source_id": "LINE-01"},          # REJECTED (invalid)
    ])
    s = summary(client)
    assert (s["duplicates"], s["conflicts"], s["unresolved"]) == (1, 1, 1)
    assert s["rejected_submissions"] == 2


def test_rejected_submissions_respects_source_filter(client):
    client.post("/api/events", json=[
        count("A-1", 501, source="LINE-01"),
        count("B-1", 999, source="LINE-02"),
        count("B-2", 600, source="LINE-02"),
        {"event_id": "X-1", "type": "COUNT", "quantity": 3},   # no source: unfiltered only
    ])
    assert summary(client)["rejected_submissions"] == 4
    assert summary(client, "LINE-01")["rejected_submissions"] == 1
    assert summary(client, "LINE-02")["rejected_submissions"] == 2
    assert summary(client, "LINE-03")["rejected_submissions"] == 0


def test_void_and_duplicate_rules_unchanged(client):
    client.post("/api/events", json=count("EV-1", 500))
    assert statuses(client.post("/api/events", json=count("EV-1", 500))) == ["DUPLICATE"]
    assert statuses(client.post("/api/events", json=void("EV-2", "EV-1"))) == ["ACCEPTED"]
    s = summary(client)
    assert s["net_total"] == 0 and s["rejected_submissions"] == 0
