from datetime import UTC, date, datetime

from sqlalchemy import text

from src.db import create_run, finish_run, is_day_collected, save_raw_response

WINDOW_START = datetime(2026, 8, 6, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 7, tzinfo=UTC)


def test_run_lifecycle_running_then_success(engine):
    run_id = create_run(engine, "backfill", WINDOW_START, WINDOW_END)

    with engine.connect() as conn:
        status, finished = conn.execute(
            text("SELECT status, finished_at FROM ingestion_runs WHERE run_id = :id"), {"id": run_id}
        ).one()
    assert status == "running"
    assert finished is None

    finish_run(engine, run_id, "success", rows_read=48)

    with engine.connect() as conn:
        status, rows_read, finished = conn.execute(
            text("SELECT status, rows_read, finished_at FROM ingestion_runs WHERE run_id = :id"),
            {"id": run_id},
        ).one()
    assert status == "success"
    assert rows_read == 48
    assert finished is not None


def test_save_raw_response_links_to_run(engine):
    run_id = create_run(engine, "backfill", WINDOW_START, WINDOW_END)
    raw_id = save_raw_response(
        engine,
        run_id=run_id,
        endpoint="/intensity/date/{date}",
        params={"date": "2026-08-06"},
        http_status=200,
        payload={"data": [{"from": "2026-08-05T23:00Z"}]},
    )

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT run_id, params->>'date' AS day, payload->'data'->0->>'from' AS first_from "
                 "FROM raw_api_responses WHERE id = :id"),
            {"id": raw_id},
        ).one()
    assert row.run_id == run_id
    assert row.day == "2026-08-06"
    assert row.first_from == "2026-08-05T23:00Z"


def test_is_day_collected_only_for_200_with_data(engine):
    day = date(2026, 8, 6)
    assert is_day_collected(engine, day) is False

    run_id = create_run(engine, "backfill", WINDOW_START, WINDOW_END)
    params = {"date": "2026-08-06"}
    endpoint = "/intensity/date/{date}"

    save_raw_response(engine, run_id, endpoint, params, 500, {"_raw_body": "boom"})
    assert is_day_collected(engine, day) is False

    save_raw_response(engine, run_id, endpoint, params, 200, {"_raw_body": "<html>"})
    assert is_day_collected(engine, day) is False

    save_raw_response(engine, run_id, endpoint, params, 200, {"data": []})
    assert is_day_collected(engine, day) is True
