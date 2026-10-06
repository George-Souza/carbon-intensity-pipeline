"""HTTP access to the Carbon Intensity API.

This module only knows HTTP. It never touches the database, and it never raises
for 4xx/5xx responses: it returns a FetchResult and lets the caller decide.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

DAY_ENDPOINT = "/intensity/date/{date}"
PAST_24H_ENDPOINT = "/intensity/{from}/pt24h"


@dataclass(frozen=True)
class FetchResult:
    endpoint: str  # path template, e.g. "/intensity/date/{date}"
    params: dict[str, str]
    status: int | None  # None when no HTTP response was received at all
    payload: Any  # parsed JSON; {"_raw_body": text} when the body is not JSON
    attempts: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None


class _Retryable(Exception):
    def __init__(self, result: FetchResult, retry_after: float | None = None) -> None:
        super().__init__(result.error)
        self.result = result
        self.retry_after = retry_after


class CarbonIntensityClient:
    def __init__(
        self,
        settings: Settings | None = None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._http = http or httpx.Client(
            base_url=self._settings.api_base_url,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(
                self._settings.http_read_timeout,
                connect=self._settings.http_connect_timeout,
            ),
        )
        self._sleep = sleep
        self._backoff = wait_exponential_jitter(
            initial=self._settings.backoff_initial_seconds,
            max=self._settings.backoff_max_seconds,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "CarbonIntensityClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch_day(self, day: date) -> FetchResult:
        iso = day.isoformat()
        return self._get(DAY_ENDPOINT, f"/intensity/date/{iso}", {"date": iso})

    def fetch_past_24h(self, from_dt: datetime) -> FetchResult:
        from_str = from_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
        return self._get(PAST_24H_ENDPOINT, f"/intensity/{from_str}/pt24h", {"from": from_str})

    def _get(self, template: str, path: str, params: dict[str, str]) -> FetchResult:
        attempt = 0

        def send() -> FetchResult:
            nonlocal attempt
            attempt += 1
            return self._send_once(template, path, params, attempt)

        retrying = Retrying(
            retry=retry_if_exception_type(_Retryable),
            stop=stop_after_attempt(self._settings.http_max_attempts),
            wait=self._wait,
            sleep=self._sleep,
            reraise=True,
        )
        try:
            return retrying(send)
        except _Retryable as exc:
            return exc.result

    def _send_once(
        self,
        template: str,
        path: str,
        params: dict[str, str],
        attempt: int,
    ) -> FetchResult:
        try:
            response = self._http.get(path)
        except httpx.TransportError as exc:
            result = FetchResult(
                endpoint=template,
                params=params,
                status=None,
                payload=None,
                attempts=attempt,
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.warning(
                "request failed, will retry",
                extra={"endpoint": template, "attempt": attempt, "error": result.error},
            )
            raise _Retryable(result) from exc

        result = self._to_result(template, params, response, attempt)

        if response.status_code >= 500 or response.status_code == 429:
            logger.warning(
                "retryable http status",
                extra={"endpoint": template, "attempt": attempt, "status": response.status_code},
            )
            raise _Retryable(result, retry_after=_parse_retry_after(response))

        if not result.ok:
            logger.error(
                "request rejected",
                extra={"endpoint": template, "status": response.status_code, "error": result.error},
            )
        return result

    def _to_result(
        self,
        template: str,
        params: dict[str, str],
        response: httpx.Response,
        attempt: int,
    ) -> FetchResult:
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"_raw_body": response.text}

        status = response.status_code
        if status != 200:
            error = _api_error_message(payload) or f"http_{status}"
        elif not (isinstance(payload, dict) and isinstance(payload.get("data"), list)):
            error = "unexpected_format"
        else:
            error = None

        return FetchResult(
            endpoint=template,
            params=params,
            status=status,
            payload=payload,
            attempts=attempt,
            error=error,
        )

    def _wait(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, _Retryable) and exc.retry_after is not None:
            return min(exc.retry_after, self._settings.backoff_max_seconds)
        return self._backoff(retry_state)


def _api_error_message(payload: Any) -> str | None:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        message = payload["error"].get("message")
        if isinstance(message, str):
            return message
    return None


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None
