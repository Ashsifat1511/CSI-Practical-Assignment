# Technical Explanation

## 1. Entity model

| Entity | Table | Key points |
|---|---|---|
| Production source | `production_sources` | Registered automatically the first time a valid event arrives from a line |
| Production event | `production_events` | One row per logical event. `event_id` is the PK (globally unique), with `UNIQUE(source_id, event_id)` as well. Stores type, quantity, target, `event_time` (device time) separately from `received_at` (server time), status (`ACCEPTED`, `PENDING_REFERENCE`, `REJECTED`), reason, normalized JSON, raw payload, channel, `voided_by_event_id`, `resolved_at`, `acknowledged_at` and `acknowledged_by` |
| Submission attempt | `submission_attempts` | Every received item, whatever happened to it: raw payload, source/event ID when present, classification, reason, channel, challenge ID, batch index |
| Acknowledgement | columns on `production_events` + `audit_log` entry | A review marker; never deletes anything |
| MQTT challenge | `mqtt_challenges` | `challenge_id` PK, SHA-256 digest of the canonical body, the body itself, the serialized response, status, error code and timestamps |
| Device link status | `mqtt_worker_status` | Single row written by the worker (state, client ID, heartbeat, last challenge, last error) |
| Audit trail | `audit_log` | Domain notifications, written after commit |

PostgreSQL protects the rules itself, not just the code:

* CHECK constraints for the COUNT shape (quantity > 0, no target) and the VOID shape (no quantity, has target)
* Partial unique index `uq_void_target_once`: at most one ACCEPTED VOID per target COUNT
* Unique `voided_by_event_id`

`net_total` = `SUM(quantity)` of ACCEPTED COUNTs that have no `voided_by_event_id`. This is the same as
"accepted COUNTs minus applied VOIDs". It is always calculated from the table, never from process memory.

## 2. Module and function boundaries

```
REST  POST /api/events ─┐
                        ├─> events.service.process_batch()
MQTT  worker.on_message ┘        └─ process_event() ── validate_event()          (pure, no DB)
      └ mqtt.service.handle_mqtt_challenge()       ├─ process_count() ─ resolve_pending_voids()
                                                   └─ process_void()
                                                   └─ events.repository (SQL only)
state.queries.get_summary()   ← used by GET /api/state AND the MQTT response
ack.service.acknowledge_events() / acknowledge_event()
audit.service.record()        ← subscribed to domain notifications
```

* **Who owns a COUNT?** `events.service.process_count()`. Nothing else in the system decides whether pieces
  are counted. Routes, the MQTT worker and the frontend never contain COUNT/VOID logic.
* **Why do REST and MQTT call the same service?** So the same rule produces the same result whichever way the
  data arrives. If a rule changes in one function (for example "VOID may cross lines"), both channels get it
  at once. The MQTT `candidate_id` is envelope metadata and never becomes part of event identity.
* Routes are thin. They parse the top-level JSON, open a transaction and call a service.
* Domain notifications (`EVENT_ACCEPTED`, `EVENT_PENDING_REFERENCE`, `VOID_RESOLVED`, `EVENT_REJECTED`,
  `EVENT_ACKNOWLEDGED`, `CHALLENGE_COMPLETED`) are queued during a transaction. They are dispatched only
  **after commit** (`shared/db.transaction()` → `domain_events.flush`) and dropped on rollback.

## 3. Transactions and duplicate strategy

| Where | Transaction |
|---|---|
| `POST /api/events` | One transaction per request, with a **SAVEPOINT per item** (`conn.transaction()` nested in `process_event`) |
| MQTT challenge | One transaction covers the challenge row, every event effect, the state read and the stored response |
| `POST /api/ack` | One transaction; each ID uses a conditional `UPDATE … WHERE acknowledged_at IS NULL` |
| Reads | Short read transaction |

Duplicate and concurrency protection:

1. `pg_advisory_xact_lock(hashtext(count_id))` serialises all work on one COUNT. The COUNT locks its own ID
   and a VOID locks its target ID. Each item takes exactly one lock, so lock ordering cannot deadlock.
2. `INSERT … ON CONFLICT DO NOTHING` on the event ID. If no row was inserted, the stored original is compared
   with the new item's **normalized** form. If they match it is a `DUPLICATE`, otherwise a `CONFLICT`.
   Normalized means trimmed strings and the timestamp converted to UTC ISO, so `10:30Z` and `12:30+02:00` are
   identical.
3. The COUNT row is locked with `SELECT … FOR UPDATE` before a VOID is applied, and the partial unique index
   guarantees a COUNT can only be reversed once even if code were wrong.
4. Acknowledgement uses an atomic conditional update, so two supervisors clicking at the same moment produce
   exactly one `ACKED`.

Tests prove this: 16 concurrent identical submissions give 1 ACCEPTED and 15 DUPLICATE, and a VOID racing its
COUNT always resolves.

## 4. Pending VOID resolution

* If a VOID arrives and its target does not exist, it is stored with status `PENDING_REFERENCE` (counted in
  `unresolved`, listed in exceptions).
* When the COUNT arrives, `resolve_pending_voids()` locks the pending VOIDs for that target **in storage order**
  (`seq`). The first one from the same source wins: it becomes ACCEPTED, is auto-acknowledged as a correction
  record, and the COUNT gets its `voided_by_event_id` set. Every other pending VOID becomes `REJECTED` with a
  clear reason ("already reversed by earlier pending VOID EV-x" or "source mismatch").
* A VOID whose target exists is checked immediately. The target must be a COUNT, from the same source, and
  not already reversed.

## 5. Restart behaviour

All state lives in PostgreSQL (named volume `pgdata`). Services keep nothing in memory except the connection
pool. After `docker compose restart` or a full `down`/`up`, totals, pending and exceptions are read back from
the tables. The MQTT worker reconnects with backoff and resubscribes. A challenge replayed after a restart is
answered from `mqtt_challenges` without reprocessing. Because the challenge, its events and its response
commit together, a crash cannot leave events processed without a stored response.

## 6. Future microservice migration

1. **Contracts first:** `shared/contracts.py` and the JSON payloads become versioned API or protobuf schemas.
2. **Notifications to a broker:** replace `domain_events.flush` with a transactional outbox table
   (`outbox(id, type, payload)`) written in the same transaction, plus a relay to Kafka or RabbitMQ.
   Subscribers such as audit become consumers.
3. **Split by module:**
   * *events service*: owns `production_events`, `submission_attempts` and `production_sources`.
   * *query service*: builds its read model from `EVENT_*` notifications, or reads a replica.
   * *ack service*: calls events through an API or owns an ack table keyed by event ID.
   * *MQTT gateway*: becomes a pure adapter that calls the events API with an idempotency key (`challenge_id`)
     and the query API. It keeps its own `mqtt_challenges` table.
4. Each service gets its own schema or database. Today's module boundaries match these splits, so no business
   rules need rewriting, only transport changes.

## 7. Assumptions and resolved ambiguities

| Topic | Assumption |
|---|---|
| Batch atomicity (§5.2/§6.1 vs §7) | A batch is one transaction with a savepoint per item. Invalid items are rejected and recorded while valid items commit, which matches the API contract. Only an unexpected system error rolls back the whole batch (HTTP 500, no partial state). |
| `event_id` uniqueness vs composite key | `event_id` is globally unique (§5.1). The composite unique key exists too. Reusing an ID on another line is a CONFLICT. |
| Rejected events | Validation and business rejects are stored only in `submission_attempts`, so ack returns `NOT_FOUND`. Pending VOIDs that lose later stay in `production_events` with `REJECTED`, so ack returns `NOT_READY`. |
| VOID acknowledgement | VOIDs are auto-acknowledged on completion (factory note §9), so the Pending table shows only COUNTs. `ALREADY_ACKED` is returned for a VOID. |
| Voided COUNTs | They stay processed and can still be acknowledged; the row is shown as "Reversed by …". |
| `pending_ack` | Number of completed events (COUNT + VOID) without acknowledgement. Because VOIDs are auto-acknowledged, in practice it counts COUNTs. |
| Quantity | Must be a JSON integer greater than 0. `5.0`, `"5"` and `true` are rejected. |
| Timestamp | ISO 8601 with an explicit offset or `Z`. A naive timestamp is rejected. |
| Challenge processing order | Parse, then challenge_id, then claim the ID (replay or conflict check), then protocol, candidate, command, expiry and events, and only then events. Envelope failures for new IDs are stored so a replay returns the same FAILED response. |
| Challenge status topic | Status messages are JSON `{protocol_version, candidate_id, client_id, status, sent_at}`. |
| Empty array | HTTP 200 with `results: []`. |
