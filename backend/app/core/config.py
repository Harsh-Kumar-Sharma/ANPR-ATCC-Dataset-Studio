import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized application configuration.

    Values may be overridden via environment variables prefixed with
    ``ANPR_`` (e.g. ``ANPR_DATABASE_URL``) or a ``.env`` file in the
    backend working directory.
    """

    model_config = SettingsConfigDict(env_prefix="ANPR_", env_file=".env", extra="ignore")

    app_name: str = "ANPR-ATCC Dataset Studio Backend"
    environment: str = "development"
    log_level: str = "INFO"

    data_dir: Path = Path("data")
    database_url: str | None = None
    workspace_root: Path = Path("data") / "workspace"
    model_weights_dir: Path = Path("data") / "models"

    #: Torch device for detection and training. ``auto`` takes the GPU
    #: when there is one; ``cpu`` forces CPU; ``cuda:<index>`` picks a
    #: specific GPU. See ``app.ml.device.resolve_device``.
    device: str = "auto"

    #: Dataloader processes and images per batch for training. Set,
    #: never left to ultralytics: its default of eight workers holds
    #: about half a gigabyte each, and on a 15 GB server shared with
    #: another system the kernel killed a medium run at epoch 13.
    train_workers: int = 2
    train_batch: int = 8

    #: Progress files and worker logs for background jobs. Lives on
    #: disk rather than in the DB so a worker can report progress
    #: without contending with the app for the SQLite write lock.
    jobs_dir: Path = Path("data") / "jobs"

    def resolved_model_weights_dir(self) -> Path:
        self.model_weights_dir.mkdir(parents=True, exist_ok=True)
        return self.model_weights_dir

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        _refuse_real_database_under_test()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = self.data_dir / "app.db"
        return f"sqlite:///{db_path.as_posix()}"


def _refuse_real_database_under_test() -> None:
    """Stop a test run falling through to the real database.

    ``tests/conftest.py`` points the app at a temporary database, but a
    conftest only loads for files beneath it. A test written anywhere
    else - a throwaway script in a scratch directory, most likely - gets
    the default instead, and the default is the user's real data. That
    has happened, and it is silent: the run passes and the database
    quietly grows a project.

    Falling back is never what a test wants, so this fails loudly and
    says how to ask for a database on purpose.
    """
    if "pytest" not in sys.modules:
        return
    raise RuntimeError(
        "Refusing to use the default database (data/app.db) from a test run: that is real data. "
        "Run tests from the backend directory so tests/conftest.py applies, or set ANPR_DATABASE_URL "
        "(and ANPR_WORKSPACE_ROOT) to somewhere disposable before importing the app."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
