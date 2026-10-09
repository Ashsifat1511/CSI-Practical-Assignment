"""Apply SQL migrations in order. Usage: python -m app.migrate [database_url]"""
import pathlib
import sys
import time

import psycopg

from app.config import settings

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def _connect(database_url: str) -> psycopg.Connection:
    for _ in range(30):
        try:
            return psycopg.connect(database_url, autocommit=True)
        except psycopg.OperationalError:
            time.sleep(1)
    raise SystemExit("database not reachable")


def migrate(database_url: str) -> list:
    applied_now = []
    with _connect(database_url) as conn:
        conn.execute("SELECT pg_advisory_lock(424242)")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
                applied_now.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(424242)")
    return applied_now


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else settings.database_url
    names = migrate(url)
    print("migrations applied:", ", ".join(names) if names else "none (schema up to date)")
