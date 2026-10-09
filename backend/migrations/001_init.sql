-- NorthBridge Garments: initial schema.

CREATE TABLE production_sources (
    source_id     TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per logical production event (the first stored copy is the original).
CREATE TABLE production_events (
    seq                 BIGSERIAL UNIQUE,
    event_id            TEXT PRIMARY KEY,               -- globally unique (brief 5.1)
    source_id           TEXT NOT NULL REFERENCES production_sources(source_id),
    type                TEXT NOT NULL CHECK (type IN ('COUNT', 'VOID')),
    quantity            INTEGER,
    target_event_id     TEXT,
    event_time          TIMESTAMPTZ NOT NULL,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    status              TEXT NOT NULL CHECK (status IN ('ACCEPTED', 'PENDING_REFERENCE', 'REJECTED')),
    reason              TEXT,
    normalized          JSONB NOT NULL,
    raw_payload         JSONB NOT NULL,
    channel             TEXT NOT NULL,
    voided_by_event_id  TEXT,
    resolved_at         TIMESTAMPTZ,
    acknowledged_at     TIMESTAMPTZ,
    acknowledged_by     TEXT,
    CONSTRAINT uq_source_event UNIQUE (source_id, event_id),
    CONSTRAINT ck_count_shape CHECK (type <> 'COUNT' OR (quantity > 0 AND target_event_id IS NULL)),
    CONSTRAINT ck_void_shape  CHECK (type <> 'VOID'  OR (quantity IS NULL AND target_event_id IS NOT NULL))
);

-- A COUNT can be reversed by at most one accepted VOID (enforced by PostgreSQL, not only by code).
CREATE UNIQUE INDEX uq_void_target_once
    ON production_events (target_event_id)
    WHERE type = 'VOID' AND status = 'ACCEPTED';
CREATE UNIQUE INDEX uq_count_voided_once
    ON production_events (voided_by_event_id)
    WHERE voided_by_event_id IS NOT NULL;
CREATE INDEX ix_events_pending_target ON production_events (target_event_id) WHERE status = 'PENDING_REFERENCE';
CREATE INDEX ix_events_source ON production_events (source_id);

-- Every received item, including duplicates, conflicts and invalid requests.
CREATE TABLE submission_attempts (
    id              BIGSERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    channel         TEXT NOT NULL,
    challenge_id    TEXT,
    batch_index     INTEGER,
    raw_payload     JSONB,
    source_id       TEXT,
    event_id        TEXT,
    classification  TEXT NOT NULL CHECK (classification IN
                        ('ACCEPTED', 'PENDING_REFERENCE', 'DUPLICATE', 'CONFLICT', 'REJECTED')),
    reason          TEXT
);
CREATE INDEX ix_attempts_class ON submission_attempts (classification);
CREATE INDEX ix_attempts_source ON submission_attempts (source_id);

CREATE TABLE mqtt_challenges (
    challenge_id    TEXT PRIMARY KEY,
    request_digest  TEXT NOT NULL,
    request_body    JSONB NOT NULL,
    response        JSONB,
    status          TEXT NOT NULL,
    error_code      TEXT,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    responded_at    TIMESTAMPTZ
);

-- Singleton row maintained by the MQTT worker, read by the dashboard.
CREATE TABLE mqtt_worker_status (
    id                    INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    candidate_id          TEXT,
    client_id             TEXT,
    connection_state      TEXT NOT NULL DEFAULT 'UNKNOWN',
    last_connected_at     TIMESTAMPTZ,
    last_heartbeat_at     TIMESTAMPTZ,
    last_challenge_id     TEXT,
    last_challenge_at     TIMESTAMPTZ,
    last_response_status  TEXT,
    last_error            TEXT,
    last_error_at         TIMESTAMPTZ,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO mqtt_worker_status (id) VALUES (1);

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    event_type  TEXT NOT NULL,
    subject_id  TEXT,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb
);
