"""Exporting when some frames' images are gone.

Two frames captured from a live stream before their images were
written took a whole export down with them: the decoder was handed an
rtsp:// address as if it were a file, and the person got
"Cannot recover full frames: source video is missing at
rtsp://admin:...@160.187.179.196:9001/Streaming/..." instead of the
forty-five perfectly good labelled frames they had worked on.

A frame whose pixels are gone is skipped and counted. Only when
nothing is left is that an error.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models.annotation import Annotation
from app.db.models.frame import Frame
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
PLATE_CLASS = 4


class _OnePlate:
    model_version = "one-plate-stub-v1"
    class_names = {0: "plate"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _labelled_project(tmp_path, name: str, frame_count: int = 6) -> tuple[dict, list[dict]]:
    """A project with every frame labelled, the ordinary exportable case."""
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(
        tmp_path / "clip.mp4", frame_count=frame_count, fps=10.0, width=WIDTH, height=HEIGHT
    )
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OnePlate(), target_fps=10.0)

    frames = client.get(f"/projects/{project['id']}/frames").json()
    for frame in frames:
        client.put(
            f"/frames/{frame['id']}/annotations",
            json={"annotations": [{"class_id": PLATE_CLASS, "bbox_json": [20.0, 20.0, 80.0, 70.0]}]},
        )
    return project, frames


def _lost_live_frame(project: dict) -> str:
    """Add exactly what a live capture used to leave behind: a frame
    of an rtsp source, labelled, with no image on disk."""
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"],
            type="rtsp",
            path_or_uri="rtsp://admin:secret@160.187.179.196:9001/Streaming/channels/101",
            fps=10.0, width=WIDTH, height=HEIGHT, duration_ms=0, frame_count=0,
        )
        db.add(source)
        db.flush()
        frame = Frame(
            source_id=source.id,
            frame_index=0,
            timestamp_ms=0,
            width=WIDTH,
            height=HEIGHT,
            status="labeled",
            image_path=None,
        )
        db.add(frame)
        db.flush()
        db.add(
            Annotation(
                frame_id=frame.id,
                source="human",
                class_id=PLATE_CLASS,
                bbox_json=[20.0, 20.0, 80.0, 70.0],
                status="accepted",
            )
        )
        db.commit()
        return frame.id


def _lose_every_image(project: dict) -> None:
    """Every labelled frame in the project, unrecoverable."""
    with SessionLocal() as db:
        for frame in db.query(Frame).join(Source).filter(Source.project_id == project["id"]).all():
            if frame.image_path:
                Path(frame.image_path).unlink(missing_ok=True)
            frame.image_path = None
            source = db.get(Source, frame.source_id)
            source.type = "rtsp"
            source.path_or_uri = "rtsp://admin:secret@160.187.179.196:9001/Streaming/channels/101"
        db.commit()


def _export(project: dict):
    return client.post(f"/projects/{project['id']}/dataset-versions", json={})


# --- the export goes ahead ----------------------------------------------------


def test_the_export_still_runs_when_one_frame_has_lost_its_image(tmp_path):
    project, frames = _labelled_project(tmp_path, "One Frame Lost")
    _lost_live_frame(project)

    response = _export(project)

    assert response.status_code in (200, 201), response.text


def test_it_says_how_many_it_left_out(tmp_path):
    """The work was done and did not land. Nobody should have to
    infer that from a count that shrank."""
    project, frames = _labelled_project(tmp_path, "Says How Many")
    _lost_live_frame(project)

    result = _export(project).json()

    assert result["frames_skipped_unrecoverable"] == 1


def test_the_frames_that_are_fine_all_go_in(tmp_path):
    project, frames = _labelled_project(tmp_path, "The Rest Go In")
    _lost_live_frame(project)

    result = _export(project).json()

    assert result["counts"]["total"] == len(frames)


def test_the_validator_warns_about_them(tmp_path):
    project, frames = _labelled_project(tmp_path, "Validator Warns")
    _lost_live_frame(project)

    result = _export(project).json()

    assert any("image is gone" in warning for warning in result["validation"]["warnings"])


def test_the_manifest_records_it(tmp_path):
    """The manifest is the record of what was written, including what
    was not."""
    import json

    project, frames = _labelled_project(tmp_path, "Manifest Records")
    _lost_live_frame(project)
    version = _export(project).json()["dataset_version"]

    manifest = json.loads(
        (Path(project["workspace_path"]) / "exports" / f"v{version['version']}" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["frames_skipped_unrecoverable"] == 1


def test_an_export_with_nothing_missing_says_zero(tmp_path):
    project, _ = _labelled_project(tmp_path, "Nothing Missing")

    result = _export(project).json()

    assert result["frames_skipped_unrecoverable"] == 0


# --- when there is nothing left -----------------------------------------------


def test_losing_every_image_is_an_error(tmp_path):
    """An empty dataset written without complaint is worse than a
    refusal."""
    project, _ = _labelled_project(tmp_path, "All Lost")
    _lose_every_image(project)

    response = _export(project)

    assert response.status_code >= 400


def test_that_error_explains_itself(tmp_path):
    project, _ = _labelled_project(tmp_path, "All Lost Message")
    _lose_every_image(project)

    message = _export(project).text

    assert "lost their images" in message
