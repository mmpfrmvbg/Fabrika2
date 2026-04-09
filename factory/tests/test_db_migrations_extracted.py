from __future__ import annotations

from factory import db, db_migrations


def test_migrate_schema_available_from_both_modules() -> None:
    assert callable(db.migrate_schema)
    assert callable(db_migrations.migrate_schema)
    assert db.migrate_schema is db_migrations.migrate_schema
