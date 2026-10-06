"""Orchestrates extraction runs: opens an ingestion run, calls the API, stores raw responses.

The run lifecycle (create -> steps -> finish) lives here so later stages (validate,
transform, load) can be added inside the same run without changing it.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine

from src.db import create_run, finish_run, is_day_collected, save_raw_response
from src.extract.client import CarbonIntensityClient, FetchResult

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_CHARS = 2000


@dataclass
class RunSummary:
    run_id: int
    status: str
    rows_read: int = 0
    days_ok: int = 0
    days_skipped: int = 0
    days_failed: int = 0
    errors: list[str] = field(default_factory=list)


def run_backfill(
    engine: Engine,
    client: CarbonIntensityClient,
    start: date,
    end: date,
    *,
    force: bool = False,
    pause_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> RunSummary:
    if start > end:
        raise ValueError(f"invalid window: start {start} is after end {end}")

    window_start = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    window_end = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    run_id = create_run(engine, "backfill", window_start, window_end)
    summary = RunSummary(run_id=run_id, status="running")

    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    calls_made = 0

    try:
        for day in days:
            if not force and is_day_collected(engine, day):
                summary.days_skipped += 1
                logger.info("day already collected, skipping", extra={"run_id": run_id, "date": day})
                continue

            if calls_made and pause_seconds:
                sleep(pause_seconds)
            calls_made += 1

            try:
                result = client.fetch_day(day)
                _store(engine, run_id, result)
                if result.ok:
                    summary.days_ok += 1
                    summary.rows_read += len(result.payload["data"])
                    logger.info("day collected", extra={"run_id": run_id, "date": day})
                else:
                    summary.days_failed += 1
                    summary.errors.append(f"{day}: {result.error}")
                    logger.error("day failed", extra={"run_id": run_id, "date": day, "error": result.error})
            except Exception as exc:
                summary.days_failed += 1
                summary.errors.append(f"{day}: {exc}")
                logger.exception("day failed unexpectedly", extra={"run_id": run_id, "date": day})

        summary.status = _backfill_status(summary)
    except Exception as exc:
        summary.status = "failed"
        summary.errors.append(str(exc))
        _finish(engine, summary)
        raise

    _finish(engine, summary)
    return summary


def run_incremental(
    engine: Engine,
    client: CarbonIntensityClient,
    now: datetime | None = None,
) -> RunSummary:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    from_dt = _floor_to_half_hour(now) - timedelta(hours=24)

    run_id = create_run(engine, "incremental", from_dt, now)
    summary = RunSummary(run_id=run_id, status="running")

    try:
        result = client.fetch_past_24h(from_dt)
        _store(engine, run_id, result)
        if result.ok:
            summary.days_ok = 1
            summary.rows_read = len(result.payload["data"])
            summary.status = "success"
        else:
            summary.days_failed = 1
            summary.errors.append(result.error or "unknown error")
            summary.status = "failed"
    except Exception as exc:
        summary.status = "failed"
        summary.errors.append(str(exc))
        _finish(engine, summary)
        raise

    _finish(engine, summary)
    return summary


def _store(engine: Engine, run_id: int, result: FetchResult) -> None:
    # No HTTP response means there is no status or body to keep (see decision 1
    # in the Day 2 plan). The failure is recorded in the log and in error_message.
    if result.status is None:
        return
    save_raw_response(
        engine,
        run_id=run_id,
        endpoint=result.endpoint,
        params=result.params,
        http_status=result.status,
        payload=result.payload,
    )


def _backfill_status(summary: RunSummary) -> str:
    if summary.days_failed == 0:
        return "success"
    if summary.days_ok > 0:
        return "partial"
    return "failed"


def _finish(engine: Engine, summary: RunSummary) -> None:
    error_message = "; ".join(summary.errors)[:MAX_ERROR_MESSAGE_CHARS] or None
    finish_run(
        engine,
        run_id=summary.run_id,
        status=summary.status,
        rows_read=summary.rows_read,
        error_message=error_message,
    )


def _floor_to_half_hour(dt: datetime) -> datetime:
    return dt.replace(minute=30 if dt.minute >= 30 else 0, second=0, microsecond=0)
