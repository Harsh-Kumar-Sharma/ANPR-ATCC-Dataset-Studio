import tempfile
from pathlib import Path

import pytest

# The DB URL must be set before app.db.session is imported anywhere,
# since it creates a module-level engine bound at import time.
_TEST_ROOT_DIR = Path(tempfile.mkdtemp(prefix="anpr-atcc-test-"))
import os  # noqa: E402

os.environ["ANPR_DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT_DIR / 'test.db').as_posix()}"
os.environ["ANPR_WORKSPACE_ROOT"] = str(_TEST_ROOT_DIR / "workspace")

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(autouse=True, scope="session")
def _create_tables():
    from app.db.base import Base
    from app.db.session import engine

    Base.metadata.create_all(bind=engine)
    yield
