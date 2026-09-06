import pytest

from app.services.rolling_buffer import RollingFrameBuffer


def test_append_and_length():
    buf: RollingFrameBuffer[str] = RollingFrameBuffer(maxlen=3)
    buf.append(0, 0, "a")
    buf.append(1, 100, "b")
    assert len(buf) == 2
    assert not buf.is_full


def test_buffer_is_full_at_maxlen():
    buf: RollingFrameBuffer[str] = RollingFrameBuffer(maxlen=2)
    buf.append(0, 0, "a")
    buf.append(1, 100, "b")
    assert buf.is_full


def test_appending_past_maxlen_evicts_oldest_and_counts_as_dropped():
    buf: RollingFrameBuffer[str] = RollingFrameBuffer(maxlen=2)
    buf.append(0, 0, "a")
    buf.append(1, 100, "b")
    buf.append(2, 200, "c")  # evicts "a"

    assert len(buf) == 2
    assert buf.dropped_count == 1
    drained = buf.drain()
    assert [item.payload for item in drained] == ["b", "c"]


def test_drain_empties_the_buffer_and_returns_oldest_first():
    buf: RollingFrameBuffer[str] = RollingFrameBuffer(maxlen=5)
    buf.append(0, 0, "a")
    buf.append(1, 100, "b")

    drained = buf.drain()
    assert [item.payload for item in drained] == ["a", "b"]
    assert len(buf) == 0


def test_drain_on_empty_buffer_returns_empty_list():
    buf: RollingFrameBuffer[str] = RollingFrameBuffer(maxlen=5)
    assert buf.drain() == []


def test_buffered_frame_carries_index_and_timestamp():
    buf: RollingFrameBuffer[str] = RollingFrameBuffer(maxlen=5)
    buf.append(7, 700, "x")
    [item] = buf.drain()
    assert item.frame_index == 7
    assert item.timestamp_ms == 700
    assert item.payload == "x"


def test_maxlen_must_be_positive():
    with pytest.raises(ValueError):
        RollingFrameBuffer(maxlen=0)
    with pytest.raises(ValueError):
        RollingFrameBuffer(maxlen=-1)


def test_no_drops_recorded_when_never_full():
    buf: RollingFrameBuffer[str] = RollingFrameBuffer(maxlen=10)
    for i in range(5):
        buf.append(i, i * 10, str(i))
    assert buf.dropped_count == 0
