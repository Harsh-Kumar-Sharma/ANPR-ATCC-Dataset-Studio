"""A test run must never reach the real database.

`tests/conftest.py` points the app at a temporary one, but a conftest
only loads for files beneath it. A test written anywhere else - a
throwaway script in a scratch directory, most likely - got the default
instead, and the default is the user's real data. That has happened:
a review run added a whole project, silently, and the run passed.

Falling back is never what a test wants, so it fails loudly now.
"""

import pytest

from app.core.config import Settings, get_settings


def test_a_test_run_refuses_the_default_database():
    """The guard itself. `Settings()` with no database URL is exactly
    what a test outside `tests/` gets."""
    with pytest.raises(RuntimeError) as raised:
        Settings(database_url=None).resolved_database_url()

    message = str(raised.value)
    assert "real data" in message
    assert "ANPR_DATABASE_URL" in message, "the error has to say how to ask for a database on purpose"


def test_an_explicit_database_url_is_still_honoured(tmp_path):
    """The guard is about the silent fallback, not about test databases.
    Naming one on purpose - which is what conftest does - still works."""
    url = f"sqlite:///{(tmp_path / 'chosen.db').as_posix()}"

    assert Settings(database_url=url).resolved_database_url() == url


def test_this_suite_is_running_against_a_temporary_database():
    """And the conftest redirect is doing its job right now."""
    url = get_settings().resolved_database_url()

    assert url.startswith("sqlite:///")
    assert "anpr-atcc-test-" in url, f"tests are pointed at {url}"
    assert not url.endswith("data/app.db")
