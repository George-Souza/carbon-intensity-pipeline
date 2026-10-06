import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from src.config import Settings
from src.extract.client import CarbonIntensityClient

BASE = "https://api.carbonintensity.org.uk"
DAY_URL = f"{BASE}/intensity/date/2026-08-06"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def day_fixture() -> dict:
    return json.loads((FIXTURES / "intensity_date_2026-08-06.json").read_text())


@pytest.fixture
def sleeps() -> list[float]:
    return []


def make_client(sleeps: list[float], **overrides) -> CarbonIntensityClient:
    values = {
        "database_url": "postgresql+psycopg://unused",
        "api_base_url": BASE,
        "backoff_initial_seconds": 0.0,
        "backoff_max_seconds": 30.0,
        **overrides,
    }
    return CarbonIntensityClient(Settings(**values), sleep=sleeps.append)


@respx.mock
def test_200_with_real_fixture_is_ok(day_fixture, sleeps):
    respx.get(DAY_URL).mock(return_value=httpx.Response(200, json=day_fixture))

    result = make_client(sleeps).fetch_day(date(2026, 8, 6))

    assert result.ok
    assert result.status == 200
    assert result.attempts == 1
    assert result.endpoint == "/intensity/date/{date}"
    assert result.params == {"date": "2026-08-06"}
    assert len(result.payload["data"]) == 48
    assert sleeps == []


@respx.mock
def test_400_is_not_retried(sleeps):
    route = respx.get(DAY_URL).mock(
        return_value=httpx.Response(
            400,
            json={"error": {"code": "400 Bad Request", "message": "Please enter a valid date"}},
        )
    )

    result = make_client(sleeps).fetch_day(date(2026, 8, 6))

    assert route.call_count == 1
    assert result.status == 400
    assert not result.ok
    assert result.error == "Please enter a valid date"
    assert "error" in result.payload
    assert sleeps == []


@respx.mock
def test_500_then_success_retries_until_ok(day_fixture, sleeps):
    route = respx.get(DAY_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json=day_fixture),
        ]
    )

    result = make_client(sleeps).fetch_day(date(2026, 8, 6))

    assert route.call_count == 3
    assert result.ok
    assert result.attempts == 3
    assert len(sleeps) == 2


@respx.mock
def test_500_on_every_attempt_gives_up_at_the_limit(sleeps):
    route = respx.get(DAY_URL).mock(return_value=httpx.Response(500, text="boom"))

    result = make_client(sleeps, http_max_attempts=4).fetch_day(date(2026, 8, 6))

    assert route.call_count == 4
    assert result.attempts == 4
    assert result.status == 500
    assert not result.ok
    assert result.payload == {"_raw_body": "boom"}


@respx.mock
def test_timeout_then_success_retries(day_fixture, sleeps):
    route = respx.get(DAY_URL).mock(
        side_effect=[httpx.ReadTimeout("timed out"), httpx.Response(200, json=day_fixture)]
    )

    result = make_client(sleeps).fetch_day(date(2026, 8, 6))

    assert route.call_count == 2
    assert result.ok
    assert result.attempts == 2


@respx.mock
def test_network_error_on_every_attempt_has_no_status(sleeps):
    respx.get(DAY_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    result = make_client(sleeps, http_max_attempts=3).fetch_day(date(2026, 8, 6))

    assert result.status is None
    assert result.payload is None
    assert result.attempts == 3
    assert result.error.startswith("ConnectError")
    assert not result.ok


@respx.mock
def test_429_waits_for_retry_after_then_retries(day_fixture, sleeps):
    respx.get(DAY_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json=day_fixture),
        ]
    )

    result = make_client(sleeps).fetch_day(date(2026, 8, 6))

    assert result.ok
    assert sleeps == [2.0]


@respx.mock
def test_200_with_non_json_body_is_recorded_as_invalid(sleeps):
    route = respx.get(DAY_URL).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))

    result = make_client(sleeps).fetch_day(date(2026, 8, 6))

    assert route.call_count == 1
    assert result.status == 200
    assert not result.ok
    assert result.payload == {"_raw_body": "<html>maintenance</html>"}


@respx.mock
def test_past_24h_formats_from_in_utc(day_fixture, sleeps):
    url = f"{BASE}/intensity/2026-10-04T12:00Z/pt24h"
    respx.get(url).mock(return_value=httpx.Response(200, json=day_fixture))

    result = make_client(sleeps).fetch_past_24h(datetime(2026, 10, 4, 12, 0, tzinfo=UTC))

    assert result.ok
    assert result.endpoint == "/intensity/{from}/pt24h"
    assert result.params == {"from": "2026-10-04T12:00Z"}
