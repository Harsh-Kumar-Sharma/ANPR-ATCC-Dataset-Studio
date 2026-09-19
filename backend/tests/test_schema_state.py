"""Whether the database matches the code.

Every time this app gains a table, the database on disk is one
migration behind until someone runs alembic. From inside the app that
looked like a 500 and "An unexpected error occurred", in whichever
panel happened to use the new table - which sends the user looking
for a bug that is not there.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app
from app.services import schema_state
from app.services.schema_state import SchemaUpgradeError

client = TestClient(app)


def _database_at(path, revision: str | None):
    """A SQLite file that claims to be at ``revision``."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    if revision is not None:
        connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    connection.execute("CREATE TABLE keepsake (id INTEGER PRIMARY KEY, note TEXT)")
    connection.execute("INSERT INTO keepsake VALUES (1, 'real data')")
    connection.commit()
    connection.close()
    return create_engine(f"sqlite:///{path.as_posix()}")


# --- knowing where it stands --------------------------------------------------


def test_a_database_one_migration_behind_is_reported_as_behind(tmp_path):
    engine = _database_at(tmp_path / "behind.db", "c81d4e0a7f36")

    state = schema_state.state(engine)

    assert state.up_to_date is False
    assert state.current == "c81d4e0a7f36"
    assert state.head != state.current


def test_it_says_which_migrations_are_pending(tmp_path):
    """"Behind" is not as useful as "behind by these"."""
    engine = _database_at(tmp_path / "behind.db", "c81d4e0a7f36")

    state = schema_state.state(engine)

    assert state.pending
    assert state.head in state.pending


def test_a_database_at_an_unknown_revision_does_not_crash_the_check(tmp_path):
    """A newer app having run against it once must not make the app
    unopenable."""
    engine = _database_at(tmp_path / "strange.db", "not-a-revision")

    state = schema_state.state(engine)

    assert state.up_to_date is False
    assert state.pending == []


def test_the_api_reports_the_state():
    response = client.get("/schema")

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"current", "head", "up_to_date", "pending"}


# --- bringing it up to date ---------------------------------------------------


def test_upgrading_brings_a_behind_database_to_head(tmp_path):
    path = tmp_path / "behind.db"
    engine = _database_at(path, "c81d4e0a7f36")

    state, _ = schema_state.upgrade(engine, path)

    assert state.up_to_date is True
    with engine.connect() as connection:
        connection.execute(text("SELECT 1 FROM training_runs"))


def test_upgrading_backs_the_database_up_first(tmp_path):
    """This is the one routine operation in the app that rewrites
    real data. An undo is worth a few megabytes."""
    path = tmp_path / "behind.db"
    engine = _database_at(path, "c81d4e0a7f36")

    _, saved = schema_state.upgrade(engine, path)

    assert saved is not None and saved.is_file()
    kept = sqlite3.connect(saved)
    assert kept.execute("SELECT note FROM keepsake").fetchone() == ("real data",)
    assert kept.execute("SELECT version_num FROM alembic_version").fetchone() == ("c81d4e0a7f36",)


def test_the_backup_is_taken_with_sqlites_own_api(tmp_path):
    """Not a file copy: in WAL mode the .db file is only the last
    checkpoint, and copying it silently loses the write-ahead log.
    That has already cost this project data once.
    """
    path = tmp_path / "wal.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE note (body TEXT)")
    connection.execute("INSERT INTO note VALUES ('written but not checkpointed')")
    connection.commit()

    saved = schema_state.backup(path)

    # The source connection is still open, so nothing has been
    # checkpointed into the .db file yet.
    kept = sqlite3.connect(saved)
    assert kept.execute("SELECT body FROM note").fetchone() == ("written but not checkpointed",)
    connection.close()


def test_a_failed_backup_stops_the_upgrade(tmp_path, monkeypatch):
    """Changing real data with no way back is the thing this is
    guarding against, so a backup that fails cancels the whole
    operation."""
    path = tmp_path / "behind.db"
    engine = _database_at(path, "c81d4e0a7f36")

    def cannot_write(_path):
        raise OSError("disk full")

    monkeypatch.setattr(schema_state, "backup", cannot_write)

    with pytest.raises(SchemaUpgradeError, match="back up"):
        schema_state.upgrade(engine, path)

    assert schema_state.state(engine).up_to_date is False, "nothing was changed"


def test_upgrading_an_already_current_database_is_harmless(tmp_path):
    path = tmp_path / "current.db"
    engine = _database_at(path, "c81d4e0a7f36")
    schema_state.upgrade(engine, path)

    state, _ = schema_state.upgrade(engine, path)

    assert state.up_to_date is True
