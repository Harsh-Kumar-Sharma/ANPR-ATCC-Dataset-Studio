"""The connection pool is never what a request waits on.

On the server, fifteen pooled connections (SQLAlchemy's default) ran
out under a page of thumbnails and a live preview, and every further
request failed after 30 seconds with "QueuePool limit reached".
"""

from app.db.session import engine

#: anyio's default thread limit: how many sync endpoints run at once.
FASTAPI_WORKER_THREADS = 40


def test_the_pool_holds_more_connections_than_requests_can_run_at_once():
    pool = engine.pool
    assert pool.size() + pool._max_overflow >= FASTAPI_WORKER_THREADS
