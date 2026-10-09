# NorthBridge Garments – Production Counter Dashboard

The first version of a production-count platform that CSI Smart Tech can demo to NorthBridge Garments. It has
one backend (a FastAPI modular monolith), one PostgreSQL database, an MQTT worker for the examiner device
simulator, and a React dashboard. **Everything runs in Docker. You only need Docker Desktop (with Compose v2).**

| Who | What they do on the dashboard |
|---|---|
| Production supervisor | Sees the true net total, reviews pending COUNT events and acknowledges them |
| Floor operator | Submits a COUNT or a VOID correction (one JSON event or a batch) |
| Support / engineering | Checks MQTT connectivity, the last challenge and response, and exceptions with reasons |

## 1. Setup

```bash
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
# edit .env: set CANDIDATE_ID to the ID assigned by the examiner, change POSTGRES_PASSWORD
docker compose up -d --build
```

| URL | What |
|---|---|
| http://localhost:8080 | Dashboard |
| http://localhost:8000/docs | OpenAPI (Swagger) for the backend |
| http://localhost:8000/api/health | Health check |

Services: `db` (PostgreSQL 16), `migrate` (runs once), `api`, `mqtt-worker`, `frontend` (nginx, proxies `/api`).

### PostgreSQL initialization / migrations

Migrations are plain SQL files in `backend/migrations/`. The `migrate` service applies them automatically before
`api` and `mqtt-worker` start. Applied versions are tracked in `schema_migrations`, so a second run does nothing.
To run it by hand:

```bash
docker compose run --rm migrate              # = python -m app.migrate
```

The test database `northbridge_test` is created by `db/init-test-db.sql` the first time the volume is initialised.

### Common commands

```bash
docker compose logs -f api mqtt-worker       # follow logs
docker compose restart api mqtt-worker       # restart: state is read back from PostgreSQL
docker compose down                          # stop (data kept in volume pgdata)
docker compose down -v                       # stop and DELETE all data
```

## 2. Tests

```bash
docker compose run --rm api pytest -q
```

There are 18 tests. They run against the separate `northbridge_test` database, never the demo data:

* Required: COUNT and total, identical duplicate not double counted, VOID before COUNT resolution,
  repeated acknowledgement, repeated MQTT challenge not reprocessed
* Extra: conflict handling, mixed batch order, competing pending VOIDs, VOID source and once-only rules,
  source filter, HTTP 400 cases, 16 concurrent identical submissions, concurrent VOID+COUNT race, restart
  (new connection pool), MQTT/REST shared state, MQTT envelope failures (expired, candidate mismatch, protocol,
  command)

## 3. REST API examples

### POST /api/events (one event or an array)

```bash
curl -s -X POST localhost:8000/api/events -H 'Content-Type: application/json' -d '{
  "source_id":"LINE-01","event_id":"EV-101","type":"COUNT","quantity":5,
  "target_event_id":null,"event_time":"2026-10-09T10:30:00Z"}'
# {"results":[{"event_id":"EV-101","status":"ACCEPTED","message":"Event processed"}]}

# same again -> DUPLICATE (total stays 5)
# same id, quantity 7 -> CONFLICT (original preserved)

curl -s -X POST localhost:8000/api/events -H 'Content-Type: application/json' -d '[
  {"source_id":"LINE-01","event_id":"EV-201","type":"VOID","target_event_id":"EV-200","event_time":"2026-10-09T10:31:00Z"},
  {"source_id":"LINE-01","event_id":"EV-200","type":"COUNT","quantity":3,"event_time":"2026-10-09T10:29:00Z"},
  {"source_id":"LINE-01","event_id":"EV-202","type":"COUNT","quantity":0,"event_time":"2026-10-09T10:29:00Z"}]'
# -> PENDING_REFERENCE, ACCEPTED ("pending VOID EV-201 resolved and applied"), REJECTED (in that order)

curl -s -X POST localhost:8000/api/events -d '"hello"'     # HTTP 400 (not an event / event array)
```

### GET /api/state

```bash
curl -s "localhost:8000/api/state?view=summary"
# {"view":"summary","source_id":null,"net_total":5,"processed_events":3,"pending_ack":2,"unresolved":0,"duplicates":1,"conflicts":1}
curl -s "localhost:8000/api/state?view=summary&source_id=LINE-01"
curl -s "localhost:8000/api/state?view=pending"
curl -s "localhost:8000/api/state?view=exceptions"
curl -s "localhost:8000/api/state?view=other"               # HTTP 400
```

### POST /api/ack

```bash
curl -s -X POST localhost:8000/api/ack -H 'Content-Type: application/json' \
  -d '{"event_ids":["EV-101","EV-101","EV-202","NOPE"]}'
# {"results":[{"event_id":"EV-101","status":"ACKED"},{"event_id":"EV-101","status":"ALREADY_ACKED"},
#             {"event_id":"EV-202","status":"NOT_FOUND"},{"event_id":"NOPE","status":"NOT_FOUND"}]}
```

`NOT_READY` is returned for a stored event that did not complete (for example a pending or rejected VOID).

### GET /api/mqtt/status (dashboard read model)

This returns the worker's connection state, candidate and client ID, last heartbeat, last challenge ID and time,
last response status, challenge counts, last error and recent challenges.

## 4. MQTT

| Setting | Value |
|---|---|
| Broker | `152.42.238.142:1883`, MQTT 3.1.1, plain TCP |
| Client ID | `fse01-{CANDIDATE_ID}-{6 random hex}` |
| Subscribe | `fse-01/{CANDIDATE_ID}/challenge` (QoS 1) |
| Publish response | `fse-01/{CANDIDATE_ID}/response` (QoS 1, retain false) |
| Publish status | `fse-01/{CANDIDATE_ID}/status` (QoS 1, retain false): `ONLINE` after subscribe, `HEARTBEAT` every 15 s, `OFFLINE` as last will and on shutdown |

Reconnects use exponential backoff (1 s to 30 s) and resubscribe on every connect.

### Sample flow

1. The simulator publishes a challenge on `.../challenge`.
2. `worker.on_message` calls `handle_mqtt_challenge(payload)`, which opens **one DB transaction**, claims the
   `challenge_id` in `mqtt_challenges` and validates the envelope (protocol, candidate, command, expiry, events).
   It then calls the same `events.service.process_batch()` used by REST and reads the six-field state with
   `state.queries.get_summary()`. Finally it stores the response and commits.
3. The worker publishes the response on `.../response` and updates `mqtt_worker_status`. The dashboard shows it
   within 5 s.
4. If the same challenge is sent again, the stored response is republished and no event is processed. If the
   same ID arrives with a changed body, the response is `FAILED / CHALLENGE_CONFLICT`.

```json
{"protocol_version":"1.0","candidate_id":"CAND-017","challenge_id":"CH-7e1c4a42","status":"COMPLETED",
 "processed_at":"2026-10-09T10:45:02Z","results":[{"event_id":"EV-101","status":"ACCEPTED","message":"Event processed"}],
 "state":{"net_total":5,"processed_events":1,"pending_ack":1,"unresolved":0,"duplicates":0,"conflicts":0}}
```

### Offline testing with a local broker

```bash
# in .env set MQTT_HOST=mosquitto, then:
docker compose --profile local-mqtt up -d
docker compose exec mosquitto mosquitto_sub -t 'fse-01/#' -v
```

## 5. Examiner demo script (dashboard)

1. Open http://localhost:8080. The header explains who uses the page and why.
2. Click **COUNT +5** and then **Submit**. Net total becomes 5, and EV-101 appears in *Pending review*.
3. Click **Submit** again. The result is DUPLICATE, the Duplicates card goes up by 1, and the net total does not change.
4. Click **VOID before COUNT** and **Submit**. The result is PENDING_REFERENCE and *Unresolved references* is 1.
   Then click **Target COUNT +3** and **Submit**. The result is ACCEPTED with "pending VOID … resolved", unresolved
   goes back to 0, the net total is unchanged and the row shows "Reversed by …".
5. Tick rows in *Pending review* and click **Acknowledge selected**. The rows disappear and *Pending
   acknowledgement* goes down.
6. Run a real simulator challenge. The MQTT panel shows the challenge ID, time and COMPLETED badge. Click
   **Conflict** or **Mixed batch** and open the *Exceptions* tab to see the reasons.

Use **New ID set** to repeat the demo with fresh event IDs.

## 6. Repository layout

```
backend/app/shared/       db pool + transaction(), contracts, domain_events bus, time helpers
backend/app/modules/events  validation.py · service.py (COUNT/VOID rules) · repository.py · routes.py
backend/app/modules/state   queries.py (summary/pending/exceptions) · routes.py
backend/app/modules/ack     service.py · repository.py · routes.py
backend/app/modules/audit   post-commit notification subscriber -> audit_log
backend/app/modules/mqtt    protocol.py · service.py · repository.py · worker.py · routes.py
backend/migrations/       SQL migrations
backend/tests/            pytest suite
frontend/                 React + Vite dashboard served by nginx
```

See `TECHNICAL_EXPLANATION.md` for the design and `AI_USAGE.md` for how AI was used.
