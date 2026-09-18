"""The progress channel between a job worker and the app.

A job runs in its own detached process, so progress has to cross a
process boundary. It crosses through a file rather than the database on
purpose: SQLite tolerates one writer, and a worker ticking progress
once a second while the API serves reads is exactly the shape that
produces lock contention. The file is owned solely by the worker and
polled by the app, so the two never contend.

The file is also why progress survives the app being closed - the
worker keeps writing to it whether or not anything is reading.

Every write replaces the whole file atomically, so a reader either sees
the previous update or the next one, never half of one. Readers still
treat a corrupt file as "nothing reported yet": on Windows the atomic
replace is the common case, not a guarantee worth betting the API on.
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Progress:
    """A single progress report. ``fraction`` is always within 0.0-1.0."""

    fraction: float
    message: str | None
    updated_at: datetime


def _clamp(fraction: float) -> float:
    return max(0.0, min(1.0, float(fraction)))


def write_progress(path: Path | str, *, fraction: float, message: str | None) -> None:
    """Replace the progress file. Safe to call as often as the worker likes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "fraction": _clamp(fraction),
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Write beside the target so os.replace stays on one filesystem, which
    # is what makes it atomic.
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def read_progress(path: Path | str) -> Progress | None:
    """The worker's latest report, or None if there is not a usable one.

    Never raises: a missing, torn or corrupt file all mean the same thing
    to a caller, which is that there is nothing to show yet.
    """
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(payload["updated_at"])
        return Progress(
            fraction=_clamp(payload["fraction"]),
            message=payload.get("message"),
            updated_at=updated_at,
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError):
        logger.debug("Unreadable progress file (treating as absent): %s", path, exc_info=True)
        return None
