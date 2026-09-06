import numpy as np

from app.services.rtsp_source import RtspAdapterConfig, RtspSourceAdapter


class FakeConnection:
    """A scripted RtspConnection: ``open_results`` controls what each
    successive ``open()`` call returns, ``frames`` controls what each
    successive ``read()`` call returns (None simulates a dropped
    frame/disconnect)."""

    instances: list["FakeConnection"] = []

    def __init__(self, open_results: list[bool], frames: list[np.ndarray | None]) -> None:
        self._open_results = list(open_results)
        self._frames = list(frames)
        self.released = False
        FakeConnection.instances.append(self)

    def open(self) -> bool:
        return self._open_results.pop(0) if self._open_results else False

    def read(self) -> np.ndarray | None:
        return self._frames.pop(0) if self._frames else None

    def release(self) -> None:
        self.released = True


def _frame() -> np.ndarray:
    return np.zeros((4, 4, 3), dtype=np.uint8)


def _no_sleep(_seconds: float) -> None:
    pass


def test_connect_success():
    conn = FakeConnection(open_results=[True], frames=[])
    adapter = RtspSourceAdapter(connection_factory=lambda: conn)
    assert adapter.connect() is True
    assert adapter.is_connected is True


def test_connect_failure():
    conn = FakeConnection(open_results=[False], frames=[])
    adapter = RtspSourceAdapter(connection_factory=lambda: conn)
    assert adapter.connect() is False
    assert adapter.is_connected is False


def test_read_frame_when_not_connected_returns_none():
    conn = FakeConnection(open_results=[False], frames=[_frame()])
    adapter = RtspSourceAdapter(connection_factory=lambda: conn)
    adapter.connect()
    assert adapter.read_frame() is None


def test_read_frame_returns_the_frame_when_connected():
    frame = _frame()
    conn = FakeConnection(open_results=[True], frames=[frame])
    adapter = RtspSourceAdapter(connection_factory=lambda: conn)
    adapter.connect()
    result = adapter.read_frame()
    assert result is frame
    assert adapter.is_connected is True


def test_a_failed_read_marks_the_adapter_disconnected():
    conn = FakeConnection(open_results=[True], frames=[None])
    adapter = RtspSourceAdapter(connection_factory=lambda: conn)
    adapter.connect()
    assert adapter.read_frame() is None
    assert adapter.is_connected is False


def test_reconnect_succeeds_on_first_retry():
    conn = FakeConnection(open_results=[True], frames=[])
    adapter = RtspSourceAdapter(
        connection_factory=lambda: conn, config=RtspAdapterConfig(max_reconnect_attempts=3), sleep=_no_sleep
    )
    assert adapter.reconnect() is True
    assert adapter.reconnect_attempts == 0  # reset on success
    assert adapter.is_connected is True


def test_reconnect_retries_with_backoff_until_it_succeeds():
    call_count = {"n": 0}

    def factory():
        call_count["n"] += 1
        # First two attempts fail to open, third succeeds.
        return FakeConnection(open_results=[call_count["n"] >= 3], frames=[])

    sleeps: list[float] = []
    adapter = RtspSourceAdapter(
        connection_factory=factory,
        config=RtspAdapterConfig(max_reconnect_attempts=5, initial_backoff_seconds=1.0, backoff_multiplier=2.0),
        sleep=sleeps.append,
    )
    assert adapter.reconnect() is True
    assert call_count["n"] == 3
    assert sleeps == [1.0, 2.0, 4.0]


def test_reconnect_gives_up_after_max_attempts():
    adapter = RtspSourceAdapter(
        connection_factory=lambda: FakeConnection(open_results=[False], frames=[]),
        config=RtspAdapterConfig(max_reconnect_attempts=3, initial_backoff_seconds=0.1),
        sleep=_no_sleep,
    )
    assert adapter.reconnect() is False
    assert adapter.reconnect_attempts == 3
    assert adapter.is_connected is False


def test_reconnect_backoff_is_capped_at_max_backoff_seconds():
    sleeps: list[float] = []
    adapter = RtspSourceAdapter(
        connection_factory=lambda: FakeConnection(open_results=[False], frames=[]),
        config=RtspAdapterConfig(
            max_reconnect_attempts=5, initial_backoff_seconds=10.0, max_backoff_seconds=15.0, backoff_multiplier=2.0
        ),
        sleep=sleeps.append,
    )
    adapter.reconnect()
    assert sleeps == [10.0, 15.0, 15.0, 15.0, 15.0]


def test_close_releases_the_connection():
    conn = FakeConnection(open_results=[True], frames=[])
    adapter = RtspSourceAdapter(connection_factory=lambda: conn)
    adapter.connect()
    adapter.close()
    assert conn.released is True
    assert adapter.is_connected is False
