"""Applies numbered SQL migrations from sql/ and tracks them in schema_migrations.

Usage: python -m src.migrate
"""

from pathlib import Path

from sqlalchemy import Engine, text

from src.db import get_engine

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

CREATE_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def discover_migrations(sql_dir: Path) -> list[tuple[str, Path]]:
    """Returns (version, path) pairs for every sql/NNN_*.sql file, sorted by version."""
    files = sorted(sql_dir.glob("*.sql"))
    return [(f.stem, f) for f in files]


def pending(applied: set[str], migrations: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    """Filters out migrations whose version is already in `applied`, preserving order."""
    return [(version, path) for version, path in migrations if version not in applied]


def get_applied_versions(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        conn.execute(text(CREATE_SCHEMA_MIGRATIONS))
        conn.commit()
        rows = conn.execute(text("SELECT version FROM schema_migrations"))
        return {row[0] for row in rows}


def apply_migration(engine: Engine, version: str, path: Path) -> None:
    sql = path.read_text()
    with engine.begin() as conn:
        conn.execute(text(sql))
        conn.execute(
            text("INSERT INTO schema_migrations (version) VALUES (:version)"),
            {"version": version},
        )


def main() -> None:
    engine = get_engine()
    applied = get_applied_versions(engine)
    migrations = discover_migrations(SQL_DIR)
    todo = pending(applied, migrations)

    if not todo:
        print("No pending migrations.")
        return

    for version, path in todo:
        print(f"Applying {version}...")
        apply_migration(engine, version, path)
        print(f"Applied {version}.")


if __name__ == "__main__":
    main()
