import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, create_engine, text

from src.config import get_settings


def get_engine(database_url: str | None = None) -> Engine:
    return create_engine(database_url or get_settings().database_url)


def create_run(
    engine: Engine,
    mode: str,
    window_start: datetime,
    window_end: datetime,
) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO ingestion_runs (mode, window_start, window_end)
                VALUES (:mode, :window_start, :window_end)
                RETURNING run_id
                """
            ),
            {"mode": mode, "window_start": window_start, "window_end": window_end},
        ).one()
        return row[0]


def finish_run(
    engine: Engine,
    run_id: int,
    status: str,
    rows_read: int,
    error_message: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE ingestion_runs
                SET status = :status,
                    rows_read = :rows_read,
                    error_message = :error_message,
                    finished_at = now()
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "rows_read": rows_read,
                "error_message": error_message,
            },
        )


def save_raw_response(
    engine: Engine,
    run_id: int,
    endpoint: str,
    params: dict[str, Any],
    http_status: int,
    payload: dict[str, Any],
) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO raw_api_responses (run_id, endpoint, params, http_status, payload)
                VALUES (:run_id, :endpoint, CAST(:params AS JSONB), :http_status, CAST(:payload AS JSONB))
                RETURNING id
                """
            ),
            {
                "run_id": run_id,
                "endpoint": endpoint,
                "params": json.dumps(params),
                "http_status": http_status,
                "payload": json.dumps(payload),
            },
        ).one()
        return row[0]


def is_day_collected(engine: Engine, day: date) -> bool:
    """True if a 200 response with a `data` key was already stored for this day."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT 1 FROM raw_api_responses
                WHERE endpoint = '/intensity/date/{date}'
                  AND params->>'date' = :day
                  AND http_status = 200
                  AND payload -> 'data' IS NOT NULL
                LIMIT 1
                """
            ),
            {"day": day.isoformat()},
        ).first()
        return row is not None
