"""Tests run against a real PostgreSQL test database (northbridge_test), never the demo database."""
import os

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
os.environ["CANDIDATE_ID"] = "CAND-TEST"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.migrate import migrate  # noqa: E402
from app.shared.db import get_pool, init_pool  # noqa: E402

TABLES = "audit_log, submission_attempts, production_events, production_sources, mqtt_challenges"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    migrate(os.environ["DATABASE_URL"])
    init_pool(os.environ["DATABASE_URL"])
    yield


@pytest.fixture(autouse=True)
def clean_db():
    with get_pool().connection() as conn:
        conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    yield


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def count(event_id, quantity=5, source="LINE-01", t="2026-10-09T10:30:00Z"):
    return {"source_id": source, "event_id": event_id, "type": "COUNT", "quantity": quantity,
            "target_event_id": None, "event_time": t}


def void(event_id, target, source="LINE-01", t="2026-10-09T10:35:00Z"):
    return {"source_id": source, "event_id": event_id, "type": "VOID", "target_event_id": target, "event_time": t}
