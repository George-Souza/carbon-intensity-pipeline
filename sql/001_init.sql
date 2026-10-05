-- Ingestion runs: one row per execution (backfill or incremental).
CREATE TABLE ingestion_runs (
    run_id         BIGSERIAL PRIMARY KEY,
    mode           TEXT NOT NULL CHECK (mode IN ('backfill', 'incremental')),
    window_start   TIMESTAMPTZ,
    window_end     TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'success', 'partial', 'failed')),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    rows_read      INT NOT NULL DEFAULT 0,
    rows_loaded    INT NOT NULL DEFAULT 0,
    rows_rejected  INT NOT NULL DEFAULT 0,
    error_message  TEXT
);

-- Raw API responses: one row per HTTP call, full payload kept for replay/debug.
CREATE TABLE raw_api_responses (
    id           BIGSERIAL PRIMARY KEY,
    run_id       BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    endpoint     TEXT NOT NULL,
    params       JSONB,
    http_status  INT NOT NULL,
    payload      JSONB NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Validated half-hourly national carbon intensity readings.
-- `index` values confirmed against the real fixture (tests/fixtures/) and the
-- official API docs: 'very low', 'low', 'moderate', 'high', 'very high'.
-- `actual` is nullable: confirmed by fetching today's data on 2026-10-05 and
-- seeing `actual` as null for the current/future half-hours while `forecast`
-- was always present -- `actual` fills in once it becomes available.
CREATE TABLE carbon_intensity_halfhourly (
    period_from      TIMESTAMPTZ PRIMARY KEY,
    period_to        TIMESTAMPTZ NOT NULL,
    forecast         INT NOT NULL CHECK (forecast >= 0),
    actual           INT CHECK (actual >= 0),
    intensity_index  TEXT NOT NULL
                      CHECK (intensity_index IN ('very low', 'low', 'moderate', 'high', 'very high')),
    first_run_id     BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    last_run_id      BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_to = period_from + INTERVAL '30 minutes')
);

-- Records that failed validation, kept for inspection. Repeats are expected
-- because the incremental run reprocesses the last 24h on every execution.
CREATE TABLE rejected_records (
    id               BIGSERIAL PRIMARY KEY,
    run_id           BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
    raw_response_id  BIGINT REFERENCES raw_api_responses(id),
    record           JSONB NOT NULL,
    reason           TEXT NOT NULL,
    rejected_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_raw_run      ON raw_api_responses (run_id);
CREATE INDEX ix_rejected_run ON rejected_records (run_id);
