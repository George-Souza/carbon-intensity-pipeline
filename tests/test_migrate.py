from pathlib import Path

from src.migrate import discover_migrations, pending


def test_discover_migrations_sorted():
    migrations = discover_migrations(Path(__file__).resolve().parent.parent / "sql")
    versions = [version for version, _ in migrations]
    assert versions == sorted(versions)
    assert "001_init" in versions


def test_pending_filters_applied():
    migrations = [
        ("001_init", Path("001_init.sql")),
        ("002_add_column", Path("002_add_column.sql")),
    ]
    assert pending(set(), migrations) == migrations
    assert pending({"001_init"}, migrations) == [migrations[1]]
    assert pending({"001_init", "002_add_column"}, migrations) == []
