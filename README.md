# carbon-intensity-pipeline

ETL pipeline that collects UK carbon intensity data from a public API
([api.carbonintensity.org.uk](https://api.carbonintensity.org.uk)), validates it, stores it in
PostgreSQL and (eventually) serves results through a REST API.

## Running the database

```bash
cp .env.example .env   # adjust credentials if you want
docker compose up
```

This starts PostgreSQL and, once it reports healthy, runs the `migrate` service, which applies
every file in `sql/` (in order) and records applied versions in `schema_migrations`. Re-running
`docker compose up` is safe — already-applied migrations are skipped.

## Collecting data

```bash
# Last 60 days, ending yesterday (UTC). Days already collected are skipped.
docker compose run --rm migrate python -m src.extract --mode backfill

# Explicit window; --force re-fetches days that were already collected.
docker compose run --rm migrate python -m src.extract --mode backfill --start 2026-08-07 --end 2026-10-05 --force

# Last 24h, re-fetched on purpose so `actual` values that arrive late get filled in.
docker compose run --rm migrate python -m src.extract --mode incremental
```

Each run is recorded in `ingestion_runs` (`success`, `partial` or `failed`). Raw API responses
are stored in `raw_api_responses`. Transformation and loading into `carbon_intensity_halfhourly`
come next.

## Local development

```bash
uv sync                 # installs dependencies into .venv
uv run pytest           # runs the test suite
```

## Notes

- Data is stored in UTC, matching what the API returns. Daily/hourly aggregations for analysis
  must convert to `Europe/London` (`AT TIME ZONE 'Europe/London'`), since the UK observes
  daylight saving time and grouping in UTC would shift the local-time pattern by an hour for
  part of the year.
- Real API responses used as test fixtures live in `tests/fixtures/`.
