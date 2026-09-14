from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _make_engine():
    url = get_settings().resolved_database_url()
    is_sqlite = url.startswith("sqlite")
    # SQLite allows one writer at a time. A detect+track run holds a
    # write transaction open for the full run (often 1-3 minutes on
    # real footage - see docs/HANDOFF.md), so any other write that
    # lands during that window needs to wait rather than fail
    # immediately: WAL mode lets readers proceed concurrently with a
    # writer, and a generous busy_timeout makes a second writer queue
    # instead of raising "database is locked" right away.
    connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
    engine = create_engine(url, connect_args=connect_args)
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
