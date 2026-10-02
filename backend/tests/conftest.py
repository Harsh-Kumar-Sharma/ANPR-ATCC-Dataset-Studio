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


@pytest.fixture(autouse=True)
def _signed_in_as_admin():
    """Every test predating sign-in calls the API as if signed in.

    Sign-in is its own concern, tested in test_auth.py, which asks for
    ``real_auth`` to put the real check back.
    """
    from app.api.auth import require_user
    from app.db.models.user import ROLE_ADMIN, User
    from app.main import app

    app.dependency_overrides[require_user] = lambda: User(
        id="test-admin", username="test-admin", display_name="Test", role=ROLE_ADMIN, is_active=True
    )
    yield
    app.dependency_overrides.pop(require_user, None)


@pytest.fixture
def real_auth(_signed_in_as_admin):
    """The real sign-in check, for tests about sign-in itself."""
    from app.api.auth import require_user
    from app.main import app

    app.dependency_overrides.pop(require_user, None)
    yield
