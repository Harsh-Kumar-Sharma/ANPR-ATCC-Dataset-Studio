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

    def resolved_model_weights_dir(self) -> Path:
        self.model_weights_dir.mkdir(parents=True, exist_ok=True)
        return self.model_weights_dir

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = self.data_dir / "app.db"
        return f"sqlite:///{db_path.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
