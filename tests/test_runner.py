import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import text

from src.config import Settings
from src.extract.client import CarbonIntensityClient
from src.extract.runner import run_backfill, run_incremental

BASE = "https://api.carbonintensity.org.uk"
FIXTURES = Path(__file__).parent / "fixtures"


def day_url(day: str) -> str:
    return f"{BASE}/intensity/date/{day}"


@pytest.fixture
def day_fixture() -> dict:
    return json.loads((FIXTURES / "intensity_date_2026-08-06.json").read_text())


def make_client(**overrides) -> CarbonIntensityClient:
    values = {
        "database_url": "postgresql+psycopg://unused",
        "api_base_url": BASE,
        "backoff_initial_seconds": 0.0,
        "backoff_max_seconds": 0.0,
        "http_max_attempts": 2,
        **overrides,
    }
    return CarbonIntensityClient(Settings(**values), sleep=lambda _: None)


def run_rows(engine) -> list:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT run_id, mode, status, rows_read, error_message FROM ingestion_runs ORDER BY run_id")
        ).all()


def raw_rows(engine) -> list:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT run_id, endpoint, params->>'date' AS day, http_status FROM raw_api_responses ORDER BY id")
        ).all()


@respx.mock
def test_backfill_stores_one_raw_row_per_day_and_closes_run(engine, day_fixture):
    for day in ["2026-08-06", "2026-08-07", "2026-08-08"]:
        respx.get(day_url(day)).mock(return_value=httpx.Response(200, json=day_fixture))

    with make_client() as client:
        summary = run_backfill(engine, client, date(2026, 8, 6), date(2026, 8, 8))

    assert summary.status == "success"
    assert summary.days_ok == 3
    assert summary.rows_read == 3 * 48

    [run] = run_rows(engine)
    assert run.status == "success"
    assert run.rows_read == 144
    assert run.error_message is None

    rows = raw_rows(engine)
    assert [r.day for r in rows] == ["2026-08-06", "2026-08-07", "2026-08-08"]
    assert all(r.run_id == summary.run_id and r.http_status == 200 for r in rows)


@respx.mock
def test_one_failing_day_is_isolated_and_run_is_partial(engine, day_fixture):
    respx.get(day_url("2026-08-06")).mock(return_value=httpx.Response(200, json=day_fixture))
    respx.get(day_url("2026-08-07")).mock(return_value=httpx.Response(500, text="boom"))
    respx.get(day_url("2026-08-08")).mock(return_value=httpx.Response(200, json=day_fixture))

    with make_client() as client:
        summary = run_backfill(engine, client, date(2026, 8, 6), date(2026, 8, 8))

    assert summary.status == "partial"
    assert summary.days_ok == 2
    assert summary.days_failed == 1

    [run] = run_rows(engine)
    assert run.status == "partial"
    assert run.rows_read == 96
    assert "2026-08-07" in run.error_message

    statuses = {r.day: r.http_status for r in raw_rows(engine)}
    assert statuses == {"2026-08-06": 200, "2026-08-07": 500, "2026-08-08": 200}


@respx.mock
def test_network_failure_on_a_day_is_not_stored_but_run_continues(engine, day_fixture):
    respx.get(day_url("2026-08-06")).mock(side_effect=httpx.ConnectError("refused"))
    respx.get(day_url("2026-08-07")).mock(return_value=httpx.Response(200, json=day_fixture))

    with make_client() as client:
        summary = run_backfill(engine, client, date(2026, 8, 6), date(2026, 8, 7))

    assert summary.status == "partial"
    assert [r.day for r in raw_rows(engine)] == ["2026-08-07"]


@respx.mock
def test_all_days_rejected_marks_run_failed(engine):
    respx.get(day_url("2026-08-06")).mock(
        return_value=httpx.Response(400, json={"error": {"code": "400", "message": "bad date"}})
    )

    with make_client() as client:
        summary = run_backfill(engine, client, date(2026, 8, 6), date(2026, 8, 6))

    assert summary.status == "failed"
    [run] = run_rows(engine)
    assert run.status == "failed"
    assert run.error_message == "2026-08-06: bad date"


@respx.mock
def test_rerun_skips_days_already_collected(engine, day_fixture):
    route = respx.get(day_url("2026-08-06")).mock(return_value=httpx.Response(200, json=day_fixture))

    with make_client() as client:
        run_backfill(engine, client, date(2026, 8, 6), date(2026, 8, 6))
        second = run_backfill(engine, client, date(2026, 8, 6), date(2026, 8, 6))

    assert route.call_count == 1
    assert second.days_skipped == 1
    assert second.status == "success"
    assert second.rows_read == 0
    assert len(raw_rows(engine)) == 1


@respx.mock
def test_force_refetches_days_already_collected(engine, day_fixture):
    route = respx.get(day_url("2026-08-06")).mock(return_value=httpx.Response(200, json=day_fixture))

    with make_client() as client:
        run_backfill(engine, client, date(2026, 8, 6), date(2026, 8, 6))
        run_backfill(engine, client, date(2026, 8, 6), date(2026, 8, 6), force=True)

    assert route.call_count == 2
    assert len(raw_rows(engine)) == 2


@respx.mock
def test_inverted_window_fails_before_any_call_or_run(engine):
    route = respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))

    with make_client() as client, pytest.raises(ValueError, match="after end"):
        run_backfill(engine, client, date(2026, 8, 8), date(2026, 8, 6))

    assert route.call_count == 0
    assert run_rows(engine) == []


@respx.mock
def test_incremental_uses_last_24h_floored_to_half_hour(engine, day_fixture):
    url = f"{BASE}/intensity/2026-10-04T12:00Z/pt24h"
    route = respx.get(url).mock(return_value=httpx.Response(200, json=day_fixture))

    with make_client() as client:
        summary = run_incremental(engine, client, now=datetime(2026, 10, 5, 12, 20, tzinfo=UTC))

    assert route.call_count == 1
    assert summary.status == "success"
    assert summary.rows_read == 48

    [run] = run_rows(engine)
    assert run.mode == "incremental"
    assert run.status == "success"


@respx.mock
def test_incremental_rejected_marks_run_failed(engine):
    respx.get(url__regex=r".*/pt24h").mock(
        return_value=httpx.Response(400, json={"error": {"code": "400", "message": "bad from"}})
    )

    with make_client() as client:
        summary = run_incremental(engine, client, now=datetime(2026, 10, 5, 12, 20, tzinfo=UTC))

    assert summary.status == "failed"
    [run] = run_rows(engine)
    assert run.status == "failed"
    assert run.error_message == "bad from"
