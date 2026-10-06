import os

import pytest
from sqlalchemy import text

from src.db import get_engine
from src.migrate import SQL_DIR, apply_migration, discover_migrations, get_applied_versions, pending


@pytest.fixture
def engine():
    """Engine on a dedicated test database. Tables are truncated before each test.

    Set TEST_DATABASE_URL to a database whose name contains "test" (for example
    carbon_intensity_test). Tests that need it are skipped when it is not set.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")

    eng = get_engine(url)
    if "test" not in eng.url.database:
        raise RuntimeError("TEST_DATABASE_URL must point to a database whose name contains 'test'")

    for version, path in pending(get_applied_versions(eng), discover_migrations(SQL_DIR)):
        apply_migration(eng, version, path)

    with eng.begin() as conn:
        conn.execute(
            text("TRUNCATE ingestion_runs, raw_api_responses, rejected_records RESTART IDENTITY CASCADE")
        )

    yield eng
    eng.dispose()
