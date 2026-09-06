from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class BufferedFrame(Generic[T]):
    frame_index: int
    timestamp_ms: int
    payload: T


class RollingFrameBuffer(Generic[T]):
    """A memory-bounded FIFO of the most recent captured frames.

    A live RTSP stream has no end, so it can never be buffered in
    full the way an offline file's frame count is known in advance
    (docs/02_IMPLEMENTATION_PLAN.md Phase 9: "bounded rolling
    buffer"). Once ``maxlen`` is reached, appending a new frame
    silently evicts the oldest one - the caller is expected to drain
    the buffer for processing faster than it fills, and eviction is
    the deliberate backpressure valve if it doesn't.
    """

    def __init__(self, maxlen: int) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self._buffer: deque[BufferedFrame[T]] = deque(maxlen=maxlen)
        self._dropped_count = 0

    @property
    def maxlen(self) -> int:
        return self._buffer.maxlen  # type: ignore[return-value]

    @property
    def dropped_count(self) -> int:
        """How many frames have ever been evicted for being too old -
        a direct signal that processing can't keep up with capture."""
        return self._dropped_count

    def __len__(self) -> int:
        return len(self._buffer)

    @property
    def is_full(self) -> bool:
        return len(self._buffer) == self.maxlen

    def append(self, frame_index: int, timestamp_ms: int, payload: T) -> None:
        if self.is_full:
            self._dropped_count += 1
        self._buffer.append(BufferedFrame(frame_index, timestamp_ms, payload))

    def drain(self) -> list[BufferedFrame[T]]:
        """Remove and return every currently-buffered frame, oldest first."""
        drained = list(self._buffer)
        self._buffer.clear()
        return drained
