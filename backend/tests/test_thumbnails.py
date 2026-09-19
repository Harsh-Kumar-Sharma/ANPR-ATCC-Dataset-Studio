"""Small pictures of frames, so two hundred can be looked at once.

"frame 0, frame 7, frame 14" says nothing about which frame has a
vehicle in it. A contact sheet does - as long as it does not cost
what two hundred full frames cost.
"""

from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.db.models.frame import Frame
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app
from app.ml.types import Detection
from app.services.thumbnails import THUMBNAIL_WIDTH, thumbs_root
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 640, 480


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 300.0, 300.0), class_id=0, confidence=0.9)]


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _video_frames(tmp_path, name: str) -> tuple[dict, list[dict]]:
    project = _project(name)
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=6, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)
    return project, client.get(f"/projects/{project['id']}/frames").json()


def _decode(content: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)


def test_a_frame_has_a_thumbnail(tmp_path):
    project, frames = _video_frames(tmp_path, "Thumbnails Exist")

    response = client.get(f"/frames/{frames[0]['id']}/thumbnail")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert _decode(response.content).shape[1] == THUMBNAIL_WIDTH


def test_the_thumbnail_is_far_smaller_than_the_frame(tmp_path):
    """Two hundred of these is the difference between a few megabytes
    and fifty."""
    project, frames = _video_frames(tmp_path, "Thumbnails Are Small")

    thumbnail = client.get(f"/frames/{frames[0]['id']}/thumbnail").content
    full = client.get(f"/frames/{frames[0]['id']}/image").content

    assert len(thumbnail) < len(full) / 2


def test_asking_for_a_thumbnail_does_not_decode_the_full_frame(tmp_path):
    """The whole point. A grid of two hundred frames from a video
    would otherwise write two hundred 1080p images to disk - the disk
    problem this project has already had once.
    """
    project, frames = _video_frames(tmp_path, "Thumbnails Are Cheap")

    for frame in frames:
        client.get(f"/frames/{frame['id']}/thumbnail")

    full_frames = Path(project["workspace_path"]) / "derived" / "frames"
    written = list(full_frames.rglob("*.jpg")) if full_frames.exists() else []
    assert written == []
    with SessionLocal() as db:
        stored = [f.image_path for f in db.query(Frame).filter(Frame.id.in_([f["id"] for f in frames]))]
    assert all(path is None for path in stored)


def test_a_thumbnail_is_made_once_and_kept(tmp_path):
    project, frames = _video_frames(tmp_path, "Thumbnails Are Cached")

    first = client.get(f"/frames/{frames[0]['id']}/thumbnail").content
    cached = list((thumbs_root(Path(project["workspace_path"]))).rglob("*.jpg"))
    second = client.get(f"/frames/{frames[0]['id']}/thumbnail").content

    assert len(cached) == 1
    assert first == second


def test_a_live_frame_uses_the_image_it_already_has(tmp_path):
    """A live capture has no video to decode from - but it wrote the
    frame, so there are pixels to shrink."""
    project = _project("Thumbnail From A Live Frame")
    saved = tmp_path / "live.jpg"
    cv2.imwrite(str(saved), np.full((HEIGHT, WIDTH, 3), 120, dtype=np.uint8))

    with SessionLocal() as db:
        source = Source(
            project_id=project["id"], type="rtsp", path_or_uri="rtsp://camera/ch1",
            fps=10.0, width=WIDTH, height=HEIGHT, duration_ms=0, frame_count=0,
        )
        db.add(source)
        db.flush()
        frame = Frame(
            source_id=source.id, frame_index=0, timestamp_ms=0,
            width=WIDTH, height=HEIGHT, image_path=str(saved),
        )
        db.add(frame)
        db.commit()
        frame_id = frame.id

    response = client.get(f"/frames/{frame_id}/thumbnail")

    assert response.status_code == 200, response.text
    assert _decode(response.content).shape[1] == THUMBNAIL_WIDTH


def test_a_live_frame_whose_image_is_gone_says_so_rather_than_erroring_vaguely(tmp_path):
    project = _project("Thumbnail With No Pixels")
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"], type="rtsp", path_or_uri="rtsp://camera/ch1",
            fps=10.0, width=WIDTH, height=HEIGHT, duration_ms=0, frame_count=0,
        )
        db.add(source)
        db.flush()
        frame = Frame(source_id=source.id, frame_index=0, timestamp_ms=0, width=WIDTH, height=HEIGHT)
        db.add(frame)
        db.commit()
        frame_id = frame.id

    response = client.get(f"/frames/{frame_id}/thumbnail")

    assert response.status_code == 400
    assert "captured live" in response.json()["message"]


def test_thumbnails_go_when_their_source_does(tmp_path):
    """Otherwise they are pictures of frames that no longer exist."""
    project, frames = _video_frames(tmp_path, "Thumbnails Cascade")
    client.get(f"/frames/{frames[0]['id']}/thumbnail")
    source_id = frames[0]["source_id"]

    client.delete(f"/projects/{project['id']}/sources/{source_id}")

    assert list(thumbs_root(Path(project["workspace_path"])).rglob("*.jpg")) == []


# --- and the hazard this created ---------------------------------------------


def test_clearing_frame_images_leaves_a_live_capture_alone(tmp_path):
    """A live capture's frames are not a cache. They are the only copy
    of pixels that went past a camera once, and clearing them would
    take those frames out of the labelling queue for good.
    """
    project = _project("Clearing Spares Live Frames")
    saved = tmp_path / "live.jpg"
    cv2.imwrite(str(saved), np.full((HEIGHT, WIDTH, 3), 120, dtype=np.uint8))
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"], type="rtsp", path_or_uri="rtsp://camera/ch1",
            fps=10.0, width=WIDTH, height=HEIGHT, duration_ms=0, frame_count=0,
        )
        db.add(source)
        db.flush()
        db.add(Frame(
            source_id=source.id, frame_index=0, timestamp_ms=0,
            width=WIDTH, height=HEIGHT, image_path=str(saved),
        ))
        db.commit()

    response = client.post("/storage/clear-frame-images", json={"project_id": project["id"]})

    assert response.status_code == 200, response.text
    assert "live source" in response.json()["detail"]
    assert saved.is_file(), "the only copy of these pixels must survive"
    assert client.get(f"/projects/{project['id']}/frames").json() != [], "and stay labellable"


def test_clearing_frame_images_still_clears_a_video_source(tmp_path):
    """Those really are a cache - the video decodes them again."""
    project, frames = _video_frames(tmp_path, "Clearing Still Works")
    client.get(f"/frames/{frames[0]['id']}/image")
    decoded = Path(project["workspace_path"]) / "derived" / "frames"
    assert list(decoded.rglob("*.jpg"))

    client.post("/storage/clear-frame-images", json={"project_id": project["id"]})

    assert list(decoded.rglob("*.jpg")) == []
    # And it comes straight back when asked for.
    assert client.get(f"/frames/{frames[0]['id']}/image").status_code == 200
