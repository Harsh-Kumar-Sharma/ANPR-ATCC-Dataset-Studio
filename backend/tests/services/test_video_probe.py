from app.services.video_probe import probe_video
from tests.video_factory import create_synthetic_video


def test_probe_reads_back_known_metadata(tmp_path):
    video_path = create_synthetic_video(tmp_path / "clip.mp4", frame_count=20, fps=10.0)

    metadata = probe_video(video_path)

    assert metadata.fps == 10.0
    assert metadata.width == 64
    assert metadata.height == 48
    assert metadata.frame_count == 20
    assert metadata.duration_ms == 2000
