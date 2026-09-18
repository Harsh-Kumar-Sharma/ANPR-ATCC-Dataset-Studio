"""Looking at, and stopping, a job's OS process.

The app supervises processes it does not own in the usual parent/child
sense - the worker is deliberately detached so it survives the app
closing, which means after a restart all the app has is a number it
wrote down earlier.

**PIDs get recycled.** A stored pid may, after a restart, belong to some
unrelated process, and terminating that would be someone else's very bad
day. So liveness takes an optional ``started_after``: a process that
started before the job existed cannot be that job's worker, however
alive it looks. Callers supervising a job should always pass it.

Implemented against the OS directly rather than adding psutil, which
would be a substantial dependency for two calls.
"""

import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: Clock skew and the gap between "row committed" and "process created"
#: are both small but non-zero; without slack the reuse guard would
#: reject the very worker it just launched.
_CREATION_TIME_SLACK = timedelta(seconds=30)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _SYNCHRONIZE = 0x00100000
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _WAIT_TIMEOUT = 0x00000102
    #: Windows FILETIME counts 100ns ticks from 1601-01-01.
    _FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

    def _open(pid: int, access: int):
        handle = ctypes.windll.kernel32.OpenProcess(access, False, pid)
        return handle or None

    def _is_alive(pid: int) -> bool:
        handle = _open(pid, _SYNCHRONIZE)
        if handle is None:
            return False
        try:
            # Still running means "has not become signalled", which avoids
            # the classic GetExitCodeProcess trap where a process that
            # exited with code 259 is indistinguishable from a live one.
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    def creation_time(pid: int) -> datetime | None:
        """When this process started, or None if that cannot be determined."""
        handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if handle is None:
            return None
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            ok = ctypes.windll.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return _FILETIME_EPOCH + timedelta(microseconds=ticks / 10)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    def _kill(pid: int) -> None:
        # /T because the worker may have spawned children of its own (a
        # trainer, say), and /F because a detached console process will
        # not act on a polite request.
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )

else:

    def _is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Exists, but belongs to someone else - which the reuse guard
            # is there to catch.
            return True
        return True

    def creation_time(pid: int) -> datetime | None:
        try:
            # Good enough to tell "started before the job" from "after";
            # not a precise boot-relative start time.
            return datetime.fromtimestamp(os.stat(f"/proc/{pid}").st_ctime, tz=timezone.utc)
        except (OSError, ValueError):
            return None

    def _kill(pid: int) -> None:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass


def is_running(pid: int | None, started_after: datetime | None = None) -> bool:
    """Is this pid a live process, and plausibly still the one we started?

    ``started_after`` should be the moment the job was created. A process
    older than that is a pid-reuse impostor and reads as not running.
    Where the platform cannot report a creation time the guard is
    skipped rather than failing closed - a job wrongly believed dead is
    a worse outcome than one wrongly believed alive, because the first
    silently abandons real work.
    """
    if pid is None or pid <= 0:
        return False
    if not _is_alive(pid):
        return False

    if started_after is not None:
        created = creation_time(pid)
        if created is not None:
            if started_after.tzinfo is None:
                started_after = started_after.replace(tzinfo=timezone.utc)
            if created < started_after - _CREATION_TIME_SLACK:
                logger.warning(
                    "pid %s started at %s, before the job it supposedly belongs to (%s) - treating as recycled",
                    pid,
                    created.isoformat(),
                    started_after.isoformat(),
                )
                return False
    return True


def terminate(pid: int | None) -> None:
    """Stop a process. A pid that is already gone is a no-op, not an error.

    Cancel races the job finishing on its own, and losing that race is
    ordinary.
    """
    if pid is None or pid <= 0:
        return
    try:
        _kill(pid)
    except Exception:
        logger.exception("Could not terminate pid %s", pid)
