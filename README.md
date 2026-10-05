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
