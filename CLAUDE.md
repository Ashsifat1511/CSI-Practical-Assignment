# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

NorthBridge Garments production-counter system: a FastAPI modular monolith with PostgreSQL, an MQTT worker for the
examiner device simulator, and a React dashboard. `assignment.md` is the original brief and is the source of
truth for business rules. `TECHNICAL_EXPLANATION.md` §7 records the decisions taken where the brief is ambiguous.

## Commands

Everything runs in Docker. No local Python, Node or PostgreSQL is installed or expected.

```bash
cp .env.example .env                                   # set CANDIDATE_ID, POSTGRES_PASSWORD
docker compose up -d --build                           # db, migrate (one-shot), api :8000, mqtt-worker, frontend :8080
docker compose up -d --build api                       # rebuild after backend changes (also rebuild mqtt-worker if touched)
docker compose up -d --build frontend                  # rebuild after frontend changes (tsc type-check runs in the build)
docker compose run --rm migrate                        # apply backend/migrations/*.sql (tracked in schema_migrations)
docker compose run --rm api pytest -q                  # full test suite
docker compose run --rm api pytest -q tests/test_events.py::test_void_before_count_resolves_automatically
docker compose logs -f api mqtt-worker
MQTT_HOST=mosquitto docker compose --profile local-mqtt up -d   # local broker instead of the examiner broker
```

* Tests run against the separate `northbridge_test` database, which is created by `db/init-test-db.sql` only when
  the `pgdata` volume is first initialised. `tests/conftest.py` migrates it and truncates the tables before each test.
* There is no linter configured.
* Do not start `mqtt-worker` against the real broker (`152.42.238.142`) with the placeholder `CAND-000`. Topics
  are per candidate, so use the local-mqtt profile for testing.

## Architecture

### Module boundaries

Each module under `backend/app/modules/` follows the same split:
* `routes.py`: thin. Parses the top-level JSON and opens a transaction.
* `service.py`: business rules.
* `repository.py`: SQL only.

Data access is raw SQL with psycopg 3; there is no ORM.

* **events** is the only place COUNT/VOID rules live. REST (`POST /api/events`) and MQTT both call
  `events.service.process_batch(conn, items, SubmissionContext)`. Never duplicate the logic in routes, the
  worker or the frontend.
* **state/queries.py** has `get_summary()`, which backs both `GET /api/state` and the `state` field of the MQTT
  response. Totals are always computed from tables:
  `net_total = SUM(quantity)` of ACCEPTED COUNTs where `voided_by_event_id IS NULL`. The summary also returns `rejected_submissions` (attempts classified REJECTED).
* **ack** uses a conditional `UPDATE … WHERE acknowledged_at IS NULL` per ID, which keeps it concurrency-safe.
* **mqtt** has three layers:
  * `protocol.py`: pure envelope validation and response builders.
  * `service.handle_mqtt_challenge()`: the business entry point.
  * `worker.py`: paho transport only (LWT OFFLINE, heartbeat thread, reconnect backoff, resubscribe on connect).
    It writes connectivity to the singleton `mqtt_worker_status` row, which `GET /api/mqtt/status` reads.
* **audit** subscribes to domain notifications and writes `audit_log`.

### Transactions and notifications

* `shared/db.transaction()` wraps one DB transaction.
* `shared/domain_events.queue()` buffers notifications in a thread-local list per transaction. They are
  dispatched only after commit and discarded on rollback. Publish notifications with `queue`, never by calling
  subscribers directly.
* Service functions take an open `conn`; the caller owns the transaction boundary:
  * REST: one transaction per request.
  * MQTT: one transaction covering the challenge row, all event effects and the stored response.
* Inside `process_event`, each batch item runs in its own SAVEPOINT (`with conn.transaction()`). Invalid items
  are rejected and recorded while valid items still commit.

### Event processing rules (see `events/service.py`)

* **Quantity limit:** COUNT quantity must be 1..500 (`MAX_COUNT_QUANTITY` in `events/validation.py`).
* **Locking:** exactly one `pg_advisory_xact_lock(hashtext(key))` per item. A COUNT locks its own ID; a VOID
  locks its target ID. Keep it to one lock per item to avoid deadlocks.
* **Duplicates and conflicts:** an existing `event_id` is compared on the `normalized` JSON (UTC ISO time,
  trimmed strings). Equal means DUPLICATE, different means CONFLICT.
* **Insert races:** a lost `INSERT … ON CONFLICT DO NOTHING` is classified the same way.
* **Global IDs:** `event_id` is globally unique (PK), so the same ID on another line is a CONFLICT.
* **Pending VOIDs:** a VOID whose target is missing is stored as `PENDING_REFERENCE`. When the COUNT arrives,
  `resolve_pending_voids()` accepts the first stored same-source VOID (by `seq`) and marks the rest REJECTED
  with a reason.
* **VOID acknowledgement:** VOIDs are auto-acknowledged on completion, so the Pending view lists COUNTs only.
* **Rejects:** validation and business rejects are recorded only in `submission_attempts`, so ack returns
  NOT_FOUND. Losing pending VOIDs stay in `production_events` as REJECTED, so ack returns NOT_READY.
* **DB guards:** CHECK constraints for the COUNT/VOID shapes and the partial unique index
  `uq_void_target_once` back the code rules.

### MQTT challenge flow

1. Parse and require a `challenge_id`.
2. Claim the ID in `mqtt_challenges` with `ON CONFLICT DO NOTHING`. If the ID exists with the same SHA-256
   digest, return the stored response without reprocessing; with a different digest, return
   `FAILED/CHALLENGE_CONFLICT`.
3. Validate protocol, candidate, command, expiry and events, in that order. Failures are stored as FAILED.
4. Run `process_batch`, then `get_summary`, then store the COMPLETED response.
5. Commit, then the worker publishes to `fse-01/{candidate}/response`.

### Schema changes

Add a new numbered file in `backend/migrations/`. Never edit `001_init.sql` once it has been applied.

### Frontend

`frontend/src/App.tsx` is a single page that polls `/api/state` (all three views) and `/api/mqtt/status` every
5 s. It shows backend values only and never computes totals. Its API client is `src/api.ts`. nginx
(`frontend/nginx.conf`) proxies `/api/` to `api:8000`. Styling is plain CSS tokens in `styles.css`, with light
and dark mode through `prefers-color-scheme`. UI copy avoids em-dashes.

## Repository conventions

* The user is the sole commit author (repo-local identity `Ashsifat1511 <sifatashrarul@gmail.com>`). Do not add
  `Co-Authored-By` trailers or mention Claude in commit messages.
* Never commit `.env`, `node_modules`, `dist` or caches (see `.gitignore`).
