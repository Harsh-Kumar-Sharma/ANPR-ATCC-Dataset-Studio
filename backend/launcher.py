"""Entry point for the PyInstaller-packaged backend.

Not used in normal `uvicorn app.main:app` development - only by the
packaged desktop build, which spawns this as a child process
(see desktop/electron/main.ts). Responsible for:

1. Pointing data/workspace/model-weight paths at a per-user, always-
   writable location (never next to the executable - an installed app
   under Program Files is often not writable without elevation).
2. Creating the database schema before the API starts serving.
3. Starting uvicorn.

Deliberately does NOT run Alembic's normal migration path. Alembic
dynamically loads `env.py`, which itself dynamically loads every
revision file under `alembic/versions/` via `importlib` from a literal
file path - a well-known pain point combining Alembic with
PyInstaller's frozen import system (confirmed here: it raised
`ModuleNotFoundError: No module named 'app'` from inside a frozen
build, even though `import app` works fine everywhere else in this
same process). Since a fresh install has no existing database to
migrate *from*, `Base.metadata.create_all()` produces the identical
end state to running every migration in order, without any of that
fragility. This only covers first-run schema creation, not upgrading
an already-installed app to a newer schema across versions - that's a
real product concern, but not one this project has an installed base
to actually need yet.
"""

import os
import sys
from pathlib import Path


def _user_data_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "ANPR-ATCC-Dataset-Studio"


def _configure_environment() -> None:
    data_root = _user_data_root()
    data_root.mkdir(parents=True, exist_ok=True)

    # Settings.__init__ reads these once per process (get_settings is
    # lru_cache'd) - must be set before the first get_settings() call,
    # which _ensure_database() below triggers.
    os.environ.setdefault("ANPR_DATA_DIR", str(data_root / "data"))
    os.environ.setdefault("ANPR_WORKSPACE_ROOT", str(data_root / "data" / "workspace"))
    os.environ.setdefault("ANPR_MODEL_WEIGHTS_DIR", str(data_root / "data" / "models"))


def _ensure_database() -> None:
    import app.db.models  # noqa: F401  (side effect: registers every model on Base.metadata)
    from app.db.base import Base
    from app.db.session import engine

    Base.metadata.create_all(bind=engine)


def main() -> None:
    _configure_environment()
    _ensure_database()

    import uvicorn

    # Pass the app *object*, not the "app.main:app" string form. The
    # string form makes uvicorn re-import the module by name at
    # runtime (needed for --reload/multi-worker) - the same class of
    # dynamic-import fragility that broke Alembic above, and it broke
    # here too (confirmed by actually running the packaged build:
    # "Could not import module app.main"). Importing directly uses the
    # same working `import app...` machinery `_ensure_database()`
    # already relies on, and skips uvicorn's own resolution entirely.
    from app.main import app as fastapi_app

    uvicorn.run(fastapi_app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
