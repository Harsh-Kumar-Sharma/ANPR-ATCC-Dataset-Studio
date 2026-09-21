"""Reviewing a detection means seeing the frame it is on.

A tight cut-out of a number plate is not something a person can judge,
and it is not something a labelling dataset can use: "this data is no
benefit to me, put the full frame with the captured number plate in".
So the timeline says, per detection, whether its whole frame can be
shown behind it.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models.frame import Frame
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96


class _OnePlate:
    model_version = "one-plate-stub-v1"
    class_names = {0: "plate"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _timeline(tmp_path, name: str) -> tuple[dict, list[dict]]:
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=6, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OnePlate(), target_fps=10.0)
    tracks = client.get(f"/projects/{project['id']}/tracks").json()
    return project, client.get(f"/tracks/{tracks[0]['id']}").json()["frames"]


def test_a_video_frame_can_always_be_shown_whole(tmp_path):
    """The file is still there, so the pixels are recoverable."""
    _, frames = _timeline(tmp_path, "Video Full Frame")

    assert all(f["full_frame"] for f in frames)


def test_a_frame_with_no_image_and_no_video_cannot(tmp_path):
    """A live stream cannot be decoded twice. Saying so is what keeps
    the review from showing a broken image."""
    _, frames = _timeline(tmp_path, "Unrecoverable Frame")
    with SessionLocal() as db:
        frame = db.get(Frame, frames[0]["frame_id"])
        db.get(Source, frame.source_id).type = "rtsp"
        frame.image_path = None
        db.commit()

    reloaded = client.get(f"/tracks/{frames[0]['track_id']}").json()["frames"]

    assert reloaded[0]["full_frame"] is False


def test_a_written_live_frame_can_be_shown_whole(tmp_path):
    project, frames = _timeline(tmp_path, "Written Live Frame")
    written = Path(project["workspace_path"]) / "kept.jpg"
    written.write_bytes(b"not really a jpeg, but it is on disk")
    with SessionLocal() as db:
        frame = db.get(Frame, frames[0]["frame_id"])
        db.get(Source, frame.source_id).type = "rtsp"
        frame.image_path = str(written)
        db.commit()

    reloaded = client.get(f"/tracks/{frames[0]['track_id']}").json()["frames"]

    assert reloaded[0]["full_frame"] is True
