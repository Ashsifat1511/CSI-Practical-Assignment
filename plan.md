# NorthBridge Garments – Production Counter Dashboard: Implementation Plan

## 0. Decisions taken (from clarification)

| Topic | Decision |
|---|---|
| Stack | Python 3.12 + FastAPI + psycopg 3 (raw SQL repositories), React + Vite + TypeScript, PostgreSQL 16, paho-mqtt 2 |
| Runtime | Everything in Docker / docker-compose: `db`, `api`, `mqtt-worker`, `frontend` (nginx). No local installs needed. |
| Candidate ID | Configured through `.env` (`CANDIDATE_ID`), placeholder `CAND-000` in `.env.example` |
| Batch semantics | One DB transaction per batch with a **SAVEPOINT per item**. Invalid / rejected items are rolled back to their savepoint and recorded; valid items commit. Only an unexpected system error rolls back the whole batch. (Resolves §5.2/§6.1 vs §7 contradiction; documented.) |
| Event identity | `event_id` is globally unique (primary key). The composite unique `(source_id, event_id)` from §7 is also created and is implied. Same `event_id` from a different source = CONFLICT. |
| Git | Repo-local identity `Ashsifat1511 <sifatashrarul@gmail.com>`, work on local branch `feature/northbridge-v1`, no co-author trailers. |

## 1. Actors, entities, rules (understanding)

* **Supervisor** – sees totals, pending COUNTs, exceptions; acknowledges reviewed events.
* **Operator** – submits manual COUNT / VOID JSON.
* **Support / engineering** – watches MQTT connectivity, last challenge, errors, full history.

Entities → tables:

| Entity | Table |
|---|---|
| Production source | `production_sources` (auto-registered on first valid event) |
| Production event (logical) | `production_events` |
| Submission attempt (every received item, incl. duplicates, conflicts, rejects) | `submission_attempts` |
| Acknowledgement | `acknowledged_at`, `acknowledged_by` on `production_events` + audit entry |
| MQTT challenge | `mqtt_challenges` |
| Device/worker connectivity | `mqtt_worker_status` (singleton row written by the worker) |
| Audit trail of domain notifications | `audit_log` |

## 2. Architecture – function-based modular monolith

```
backend/app/
  main.py                    FastAPI app factory, router wiring, error handlers
  config.py                  env settings
  migrate.py                 `python -m app.migrate` – applies migrations/*.sql
  shared/
    db.py                    connection pool, transaction() helper
    contracts.py             dataclasses / enums: EventStatus, ItemResult, Summary
    domain_events.py         in-process bus; publish_after_commit()
    timeutil.py
  modules/
    events/  validation.py   validate_event(raw) -> ValidEvent | ValidationError
             service.py      process_batch(conn, items, channel, ...) ;
                             process_event(), process_count(), process_void(),
                             resolve_pending_voids()
             repository.py   SQL only
             routes.py       POST /api/events  (thin)
    ack/     service.py      acknowledge_events(); acknowledge_event()
             repository.py, routes.py  POST /api/ack
    state/   queries.py      get_summary(), get_pending(), get_exceptions()
             service.py, routes.py     GET /api/state
    audit/   service.py      subscribes to domain events, writes audit_log
             repository.py
    mqtt/    protocol.py     envelope validation, response builders, digest
             service.py      handle_mqtt_challenge(payload, now) -> response
             repository.py   challenge + worker status SQL
             worker.py       paho client: connect, LWT, subscribe, heartbeat, backoff
             routes.py       GET /api/mqtt/status (dashboard read model)
backend/migrations/001_init.sql
backend/tests/
frontend/ (React dashboard, nginx proxies /api -> api:8000)
```

Flow: `REST route ─┐`
`              ├─> events.service.process_batch() -> validate_event() -> process_count()/process_void() -> repository -> PostgreSQL`
`MQTT worker ──┘` (MQTT uses the same function inside the challenge transaction and then `state.queries.get_summary()`).

Domain notifications `EVENT_ACCEPTED`, `EVENT_PENDING`, `VOID_RESOLVED`, `EVENT_REJECTED`, `EVENT_ACKNOWLEDGED`, `CHALLENGE_COMPLETED` are collected during the transaction and dispatched **only after commit**.

## 3. Database model (migrations/001_init.sql)

* `production_sources(source_id PK, display_name, created_at)`
* `production_events(event_id PK, source_id FK, type CHECK IN (COUNT,VOID), quantity, target_event_id, event_time timestamptz, received_at, status CHECK IN (ACCEPTED, PENDING_REFERENCE, REJECTED), reason, normalized jsonb, raw_payload jsonb, channel, voided_by_event_id, resolved_at, acknowledged_at, acknowledged_by)`
  * `UNIQUE(source_id, event_id)`
  * partial unique index `ON (target_event_id) WHERE type='VOID' AND status='ACCEPTED'` → one COUNT reversed at most once, enforced by PostgreSQL.
  * CHECKs: COUNT ⇒ quantity>0 and target null; VOID ⇒ quantity null, target not null.
* `submission_attempts(id bigserial, received_at, channel, challenge_id, batch_index, raw_payload jsonb, source_id, event_id, classification, reason)`
* `mqtt_challenges(challenge_id PK, request_digest, request_body jsonb, response jsonb, status, error_code, received_at, responded_at)`
* `mqtt_worker_status(id=1, candidate_id, client_id, connection_state, last_heartbeat_at, last_challenge_id, last_challenge_at, last_response_status, last_error, last_error_at, updated_at)`
* `audit_log(id, at, event_type, event_id, payload jsonb)`

net_total = `SUM(quantity)` of ACCEPTED COUNTs with `voided_by_event_id IS NULL` (durable evidence, no memory).

## 4. Business logic (events.service)

1. `validate_event(raw)` – shape, non-empty strings, type, quantity positive int (bool/float rejected), target rules, ISO-8601 with timezone. Returns normalized dict (event_time → UTC ISO).
2. `process_event(conn, raw, ctx)` inside SAVEPOINT:
   * invalid → REJECTED (attempt recorded).
   * `pg_advisory_xact_lock(hashtext(count_id))` where count_id = own id for COUNT, target id for VOID → serialises COUNT/VOID races.
   * `INSERT ... ON CONFLICT DO NOTHING`; if existing row: same normalized → DUPLICATE, different → CONFLICT (original preserved).
   * COUNT → ACCEPTED, then `resolve_pending_voids(count)`: pending VOIDs for it ordered by `received_at, seq`; first with same source wins (ACCEPTED, auto-acknowledged, COUNT.voided_by set); others → REJECTED with reason.
   * VOID → `process_void`: target missing → PENDING_REFERENCE; target is VOID / different source / already voided / self → REJECTED (not stored as logical event, attempt stored); else ACCEPTED, `SELECT … FOR UPDATE` on COUNT, set voided_by, auto-ack VOID.
3. Every item writes a `submission_attempts` row with its classification (after savepoint rollback for rejects).

## 5. REST APIs

* `POST /api/events` – object or array; 200 with ordered `results[{event_id,status,message}]`; 400 for non-JSON / non-object/array top level.
* `GET /api/state?view=summary|pending|exceptions&source_id=` – 400 for bad view.
* `POST /api/ack {"event_ids":[...], "acknowledged_by": optional}` – ordered ACKED / ALREADY_ACKED / NOT_READY / NOT_FOUND, conditional `UPDATE … WHERE acknowledged_at IS NULL` in one transaction.
* Extra (read model for dashboard): `GET /api/mqtt/status`, `GET /api/health`.
* Generic error handler: no stack traces / SQL in responses.

## 6. MQTT worker

* Client ID `fse01-{candidate_id}-{6 hex}`, MQTT 3.1.1, QoS 1, retain false, LWT OFFLINE on status topic.
* on_connect → subscribe `fse-01/{cid}/challenge` → publish ONLINE; heartbeat thread every 15 s; paho `reconnect_delay_set(1, 30)` backoff, resubscribe in on_connect.
* `handle_mqtt_challenge(payload, now)` (one DB transaction):
  1. parse JSON / object, challenge_id → else FAILED VALIDATION_ERROR
  2. protocol_version == "1.0" → else UNSUPPORTED_PROTOCOL
  3. candidate_id == configured → else CANDIDATE_MISMATCH
  4. `INSERT mqtt_challenges ON CONFLICT DO NOTHING`; existing with same digest → return stored response (no reprocessing); different digest → FAILED CHALLENGE_CONFLICT
  5. command / events list → VALIDATION_ERROR; expires_at past → CHALLENGE_EXPIRED
  6. `events.service.process_batch(conn, events, channel="MQTT")` + `state.queries.get_summary(conn)` → COMPLETED response, stored, committed, then published.
  7. Unexpected → FAILED INTERNAL_ERROR.
* Worker status persisted to `mqtt_worker_status`; dashboard reads it through the API.

## 7. Frontend (single responsive page)

Header (who/why) · six KPI cards · JSON submit panel (examples: COUNT, duplicate, VOID-before-COUNT, mixed batch) with per-item results · Pending/Exceptions tab table with checkbox multi-select + Acknowledge · MQTT panel (state, candidate ID, client ID, heartbeat, last challenge ID/time, last response status, counts, last error, recent challenges). Loading / empty / error / invalid JSON states; auto-refresh 5 s; source filter.

## 8. Tests (pytest against real PostgreSQL test DB in docker)

1. COUNT updates total. 2. Identical duplicate not double counted (DUPLICATE attempt stored). 3. VOID before COUNT resolves automatically. 4. Repeated acknowledgement (ACKED → ALREADY_ACKED, in-request duplicates). 5. Repeated MQTT challenge not reprocessed; changed body → CHALLENGE_CONFLICT.
Extra: conflict, mixed batch order, competing VOIDs, concurrent duplicate submissions (threads), expired / mismatched challenge, HTTP 400, restart (new pool) keeps state.

Run: `docker compose run --rm api pytest -q`.

## 9. Docs & delivery

README.md (setup, migrate, run, test, curl examples, MQTT topics, sample flow), TECHNICAL_EXPLANATION.md (entity model, boundaries, transactions, duplicates, pending VOID, restart, microservice migration, assumptions), AI_USAGE.md, `.env.example`, `.gitignore`, `docs/screenshots/` placeholder.

## 10. Commit plan (branch `feature/northbridge-v1`)

1. plan + scaffold + docker-compose
2. database schema + migration runner
3. events module (validation, COUNT/VOID, duplicates, pending resolution) + REST
4. state + ack + audit modules
5. MQTT worker + challenge service
6. frontend dashboard
7. automated tests
8. documentation
