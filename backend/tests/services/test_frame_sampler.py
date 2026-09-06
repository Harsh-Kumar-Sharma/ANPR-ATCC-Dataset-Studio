import pytest

from app.services.frame_sampler import sample_frame_timestamps


def test_sampling_is_deterministic_across_calls():
    first = sample_frame_timestamps(frame_count=300, native_fps=30.0, target_fps=5.0)
    second = sample_frame_timestamps(frame_count=300, native_fps=30.0, target_fps=5.0)
    assert first == second


def test_sampling_interval_matches_target_fps():
    # native 30fps sampled at 5fps -> every 6th frame.
    sampled = sample_frame_timestamps(frame_count=30, native_fps=30.0, target_fps=5.0)
    assert [s.frame_index for s in sampled] == [0, 6, 12, 18, 24]


def test_timestamps_are_derived_from_frame_index_and_native_fps():
    sampled = sample_frame_timestamps(frame_count=30, native_fps=30.0, target_fps=5.0)
    assert [s.timestamp_ms for s in sampled] == [0, 200, 400, 600, 800]


def test_target_fps_higher_than_native_still_samples_every_frame():
    sampled = sample_frame_timestamps(frame_count=5, native_fps=10.0, target_fps=60.0)
    assert [s.frame_index for s in sampled] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("frame_count,native_fps,target_fps", [(0, 30.0, 5.0), (10, 0, 5.0), (10, 30.0, 0)])
def test_invalid_inputs_are_rejected(frame_count, native_fps, target_fps):
    with pytest.raises(ValueError):
        sample_frame_timestamps(frame_count=frame_count, native_fps=native_fps, target_fps=target_fps)
