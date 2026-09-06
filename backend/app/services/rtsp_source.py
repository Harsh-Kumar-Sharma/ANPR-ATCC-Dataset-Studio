import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np


class RtspConnection(Protocol):
    """A single, non-reconnecting connection attempt. RtspSourceAdapter
    owns reconnection; a connection object owns nothing beyond "am I
    open and can I hand back a frame"."""

    def open(self) -> bool: ...
    def read(self) -> np.ndarray | None: ...
    def release(self) -> None: ...


class OpenCvRtspConnection:
    """Real RTSP connection via OpenCV/FFmpeg - the same
    ``cv2.VideoCapture`` machinery already used for offline video
    files (see docs/services/video_probe.py, frame_sampler.py); RTSP
    URLs are just another source string FFmpeg's demuxer accepts."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._capture = None

    def open(self) -> bool:
        import cv2

        self._capture = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
        return bool(self._capture.isOpened())

    def read(self) -> np.ndarray | None:
        if self._capture is None:
            return None
        ok, frame = self._capture.read()
        return frame if ok else None

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


ConnectionFactory = Callable[[], RtspConnection]
ConnectionProvider = Callable[[str], RtspConnection]


def default_connection_provider(url: str) -> RtspConnection:
    """The real connection provider. Injected as a FastAPI dependency
    (see app/api/rtsp.py) so tests can substitute a fake without ever
    attempting a real, slow (up to 30s to time out) network connection."""
    return OpenCvRtspConnection(url)


@dataclass(frozen=True)
class RtspAdapterConfig:
    max_reconnect_attempts: int = 5
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    backoff_multiplier: float = 2.0


class RtspSourceAdapter:
    """A reconnecting RTSP source (docs/02_IMPLEMENTATION_PLAN.md
    Phase 9: "RTSP source adapter" + "reconnect behavior").

    Connection creation is injected via ``connection_factory`` so this
    class - the actual reconnect state machine - can be fully unit
    tested without a real network or RTSP server. ``sleep`` is
    likewise injectable so backoff tests don't have to actually wait.
    """

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        config: RtspAdapterConfig = RtspAdapterConfig(),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._connection_factory = connection_factory
        self._config = config
        self._sleep = sleep
        self._connection: RtspConnection | None = None
        self.is_connected = False
        self.reconnect_attempts = 0

    def connect(self) -> bool:
        self._connection = self._connection_factory()
        self.is_connected = self._connection.open()
        return self.is_connected

    def read_frame(self) -> np.ndarray | None:
        if not self.is_connected or self._connection is None:
            return None
        frame = self._connection.read()
        if frame is None:
            self.is_connected = False  # a failed read means the stream dropped
        return frame

    def reconnect(
        self,
        should_stop: Callable[[], bool] | None = None,
        on_attempt: Callable[[int], None] | None = None,
    ) -> bool:
        """Exponential backoff up to ``max_reconnect_attempts``.
        Returns True once reconnected, False once attempts are
        exhausted (the caller decides what "give up" means - e.g.
        marking the processing run failed).

        ``should_stop``, checked before and after each backoff sleep,
        lets a caller abandon reconnection promptly instead of
        blocking through however much backoff remains - without it, a
        user's "stop" request could sit unanswered for the whole
        backoff sequence (up to ~31s with the default config).

        ``on_attempt`` fires as soon as each attempt number is
        decided, before that attempt's backoff sleep - without it, a
        caller polling ``reconnect_attempts`` from another thread
        would see nothing update until this whole (possibly
        long-running) call returns.
        """
        if self._connection is not None:
            self._connection.release()

        backoff = self._config.initial_backoff_seconds
        for attempt in range(1, self._config.max_reconnect_attempts + 1):
            if should_stop is not None and should_stop():
                return False
            self.reconnect_attempts = attempt
            if on_attempt is not None:
                on_attempt(attempt)
            self._sleep(backoff)
            if should_stop is not None and should_stop():
                return False
            if self.connect():
                self.reconnect_attempts = 0
                return True
            backoff = min(backoff * self._config.backoff_multiplier, self._config.max_backoff_seconds)
        return False

    def close(self) -> None:
        if self._connection is not None:
            self._connection.release()
            self._connection = None
        self.is_connected = False
