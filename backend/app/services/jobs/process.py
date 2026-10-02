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
    _ERROR_ACCESS_DENIED = 5
    #: Windows FILETIME counts 100ns ticks from 1601-01-01.
    _FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

    # Declared explicitly. A HANDLE is pointer-sized, and ctypes defaults
    # every return value to a 32-bit signed int - which silently truncates
    # any handle above 0x7FFFFFFF, so the handle that gets closed is not
    # the handle that was opened.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )

    def _open(pid: int, access: int) -> tuple[int | None, int]:
        """Returns (handle, last_error). A null handle with ERROR_ACCESS_DENIED
        means the process exists but is not ours to inspect."""
        handle = _kernel32.OpenProcess(access, False, pid)
        if not handle:
            return None, ctypes.get_last_error()
        return handle, 0

    def _is_alive(pid: int) -> bool:
        handle, error = _open(pid, _SYNCHRONIZE)
        if handle is None:
            # Access denied means alive-but-protected, which must read as
            # running: treating it as dead is how reconciliation ends up
            # abandoning a job that is still doing real work.
            return error == _ERROR_ACCESS_DENIED
        try:
            # "Has not become signalled" rather than GetExitCodeProcess,
            # which cannot distinguish a live process from one that exited
            # with code 259 (STILL_ACTIVE).
            return _kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            _kernel32.CloseHandle(handle)

    def creation_time(pid: int) -> datetime | None:
        """When this process started, or None if that cannot be determined."""
        handle, _ = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if handle is None:
            return None
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not _kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            # Integer division: a FILETIME is far past the 2^53 where float
            # arithmetic starts rounding timestamps into the wrong second.
            return _FILETIME_EPOCH + timedelta(microseconds=ticks // 10)
        finally:
            _kernel32.CloseHandle(handle)

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
            # Exists, but belongs to someone else - same reasoning as the
            # access-denied case on Windows.
            return True
        # A zombie has exited but not been waited for, and still answers
        # to its pid. It is running nothing.
        return not _is_zombie(pid)

    def _is_zombie(pid: int) -> bool:
        """Linux only, via /proc; elsewhere a zombie reads as alive."""
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as stat:
                # "pid (comm) state ..." - comm may itself hold spaces or
                # parentheses, so split after the last one.
                return stat.read().rsplit(")", 1)[1].split()[0] == "Z"
        except (OSError, IndexError):
            return False

    def creation_time(pid: int) -> datetime | None:
        """When this process started, or None where the platform cannot say.

        Linux only: /proc does not exist on macOS or the BSDs, where this
        returns None and the pid-reuse guard is skipped. Adding psutil
        would cover them, at the cost of a substantial dependency for one
        call.
        """
        try:
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
