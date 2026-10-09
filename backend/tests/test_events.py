import json
from concurrent.futures import ThreadPoolExecutor

from tests.conftest import count, void


def summary(client, source=None):
    url = "/api/state?view=summary" + (f"&source_id={source}" if source else "")
    return client.get(url).json()


def statuses(resp):
    return [r["status"] for r in resp.json()["results"]]


# --- required ------------------------------------------------------------------------------------

def test_count_updates_total(client):
    resp = client.post("/api/events", json=count("EV-101", 5))
    assert resp.status_code == 200
    assert resp.json()["results"] == [{"event_id": "EV-101", "status": "ACCEPTED", "message": "Event processed"}]
    s = summary(client)
    assert s["net_total"] == 5
    assert s["processed_events"] == 1
    assert s["pending_ack"] == 1


def test_identical_duplicate_is_not_double_counted(client):
    client.post("/api/events", json=count("EV-101", 5))
    # Same data, timestamp written in a different but equivalent form -> still a duplicate.
    again = count("EV-101", 5, t="2026-10-09T12:30:00+02:00")
    resp = client.post("/api/events", json=again)
    assert statuses(resp) == ["DUPLICATE"]
    s = summary(client)
    assert s["net_total"] == 5
    assert s["processed_events"] == 1
    assert s["duplicates"] == 1


def test_void_before_count_resolves_automatically(client):
    resp = client.post("/api/events", json=void("EV-201", "EV-200"))
    assert statuses(resp) == ["PENDING_REFERENCE"]
    assert summary(client)["unresolved"] == 1

    resp = client.post("/api/events", json=count("EV-200", 3))
    assert statuses(resp) == ["ACCEPTED"]
    s = summary(client)
    assert s["unresolved"] == 0
    assert s["net_total"] == 0
    assert s["processed_events"] == 2
    # The VOID is a correction record: auto-acknowledged, so only the COUNT waits for review.
    assert s["pending_ack"] == 1
    pending = client.get("/api/state?view=pending").json()["items"]
    assert [p["event_id"] for p in pending] == ["EV-200"]


def test_repeated_acknowledgement_is_safe(client):
    client.post("/api/events", json=[count("EV-101"), void("EV-301", "EV-300")])
    resp = client.post("/api/ack", json={"event_ids": ["EV-101", "EV-101", "EV-301", "NOPE"]})
    assert resp.status_code == 200
    assert [r["status"] for r in resp.json()["results"]] == ["ACKED", "ALREADY_ACKED", "NOT_READY", "NOT_FOUND"]

    resp = client.post("/api/ack", json={"event_ids": ["EV-101"]})
    assert resp.json()["results"] == [{"event_id": "EV-101", "status": "ALREADY_ACKED"}]
    assert summary(client)["pending_ack"] == 0
    # Acknowledgement does not block a later valid correction.
    assert statuses(client.post("/api/events", json=void("EV-102", "EV-101"))) == ["ACCEPTED"]
    assert summary(client)["net_total"] == 0


# --- extra reliability ---------------------------------------------------------------------------

def test_conflict_preserves_original(client):
    client.post("/api/events", json=count("EV-101", 5))
    assert statuses(client.post("/api/events", json=count("EV-101", 9))) == ["CONFLICT"]
    assert statuses(client.post("/api/events", json=count("EV-101", 5, source="LINE-02"))) == ["CONFLICT"]
    s = summary(client)
    assert s["net_total"] == 5 and s["conflicts"] == 2
    kinds = [e["kind"] for e in client.get("/api/state?view=exceptions").json()["items"]]
    assert kinds.count("CONFLICT") == 2


def test_mixed_batch_keeps_order_and_valid_items(client):
    batch = [count("EV-1", 4), {"source_id": "LINE-01", "event_id": "EV-2", "type": "COUNT", "quantity": 0,
                                "event_time": "2026-10-09T10:30:00Z"},
             "not an object", count("EV-3", 2, t="2026-10-09T10:30:00"), void("EV-4", "EV-1"), count("EV-1", 4)]
    resp = client.post("/api/events", json=batch)
    assert resp.status_code == 200
    assert statuses(resp) == ["ACCEPTED", "REJECTED", "REJECTED", "REJECTED", "ACCEPTED", "DUPLICATE"]
    assert summary(client)["net_total"] == 0
    exceptions = client.get("/api/state?view=exceptions").json()["items"]
    assert sum(1 for e in exceptions if e["kind"] == "REJECTED") == 3


def test_competing_pending_voids_first_wins(client):
    client.post("/api/events", json=[void("EV-11", "EV-10"), void("EV-12", "EV-10")])
    client.post("/api/events", json=count("EV-10", 7))
    s = summary(client)
    assert s["net_total"] == 0 and s["unresolved"] == 0
    exc = client.get("/api/state?view=exceptions").json()["items"]
    loser = [e for e in exc if e["event_id"] == "EV-12"][0]
    assert "already reversed by earlier pending VOID EV-11" in loser["reason"]


def test_void_rules(client):
    client.post("/api/events", json=count("EV-1", 4))
    assert statuses(client.post("/api/events", json=void("EV-2", "EV-1", source="LINE-09"))) == ["REJECTED"]
    assert statuses(client.post("/api/events", json=void("EV-3", "EV-1"))) == ["ACCEPTED"]
    assert statuses(client.post("/api/events", json=void("EV-4", "EV-1"))) == ["REJECTED"]
    assert summary(client)["net_total"] == 0


def test_source_filter(client):
    client.post("/api/events", json=[count("A-1", 5), count("B-1", 3, source="LINE-02"), count("B-1", 3, source="LINE-02")])
    assert summary(client, "LINE-01")["net_total"] == 5
    s2 = summary(client, "LINE-02")
    assert s2["net_total"] == 3 and s2["duplicates"] == 1


def test_bad_top_level_returns_400(client):
    assert client.post("/api/events", content=b"{oops").status_code == 400
    assert client.post("/api/events", content=json.dumps("x")).status_code == 400
    assert client.get("/api/state?view=bogus").status_code == 400
    assert client.post("/api/ack", json={"ids": []}).status_code == 400


def test_concurrent_identical_submissions_count_once(client):
    def send(_):
        return client.post("/api/events", json=count("EV-RACE", 6)).json()["results"][0]["status"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(16)))
    assert results.count("ACCEPTED") == 1
    assert results.count("DUPLICATE") == 15
    assert summary(client)["net_total"] == 6


def test_concurrent_void_and_count(client):
    def send(body):
        return client.post("/api/events", json=body).json()["results"][0]["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(send, [void("EV-V", "EV-C"), count("EV-C", 5)]))
    s = summary(client)
    assert s["net_total"] == 0 and s["unresolved"] == 0


def test_state_survives_restart(client):
    from app.shared.db import init_pool
    import os

    client.post("/api/events", json=count("EV-1", 8))
    init_pool(os.environ["DATABASE_URL"])  # brand-new pool = simulated process restart
    assert summary(client)["net_total"] == 8
