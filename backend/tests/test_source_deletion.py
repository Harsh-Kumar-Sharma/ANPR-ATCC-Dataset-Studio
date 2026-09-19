"""Removing a source, and everything that has to be true first.

A source owns the frames sampled from it, the runs that processed it,
the tracks and detections found in it, every label drawn on those
frames, its copy of the video, and the crops and frame images derived
from it. Deleting one is the project delete in miniature, and it wants
the same interlocks.

What it must *not* touch is a dataset version already exported. Those
are immutable by design and shared with the project's other sources.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.ml.types import Detection
from tests.job_execution import deferred_jobs, process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
CAR_CLASS = 4


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _source(project: dict, tmp_path, name: str, *, process: bool = True, label: bool = True) -> dict:
    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    if not process:
        return source

    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)
    if label:
        frames = client.get(f"/projects/{project['id']}/frames", params={"source_id": source["id"]}).json()
        if not frames:
            frames = client.get(f"/projects/{project['id']}/frames").json()
        saved = client.put(
            f"/frames/{frames[0]['id']}/annotations",
            json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
        )
        assert saved.status_code == 200, saved.text
    return source


def _contents(project: dict, source: dict) -> dict:
    response = client.get(f"/projects/{project['id']}/sources/{source['id']}/contents")
    assert response.status_code == 200, response.text
    return response.json()


def _delete(project: dict, source: dict):
    return client.delete(f"/projects/{project['id']}/sources/{source['id']}")


# --- what is in there ---------------------------------------------------------


def test_the_contents_say_what_deleting_would_destroy(tmp_path):
    project = _project("Source Contents")
    source = _source(project, tmp_path, "clip")

    contents = _contents(project, source)

    assert contents["frames"] > 0
    assert contents["tracks"] == 1
    assert contents["labels"] == 1
    assert contents["bytes"] > 0, "its copy of the video and its crops are the bulk of it"
    assert contents["running_jobs"] == 0


def test_an_untouched_source_reports_only_its_video(tmp_path):
    project = _project("Untouched Source")
    source = _source(project, tmp_path, "unprocessed", process=False)

    contents = _contents(project, source)

    assert contents["frames"] == 0
    assert contents["tracks"] == 0
    assert contents["labels"] == 0
    assert contents["bytes"] > 0


# --- deleting -----------------------------------------------------------------


def test_deleting_a_source_removes_it_from_the_project(tmp_path):
    project = _project("Delete Source")
    source = _source(project, tmp_path, "doomed")

    response = _delete(project, source)

    assert response.status_code == 200, response.text
    assert source["id"] not in [s["id"] for s in client.get(f"/projects/{project['id']}/sources").json()]
    assert client.get(f"/projects/{project['id']}/sources/{source['id']}").status_code == 404


def test_everything_the_source_owned_goes_with_it(tmp_path):
    from app.db.models import Annotation, Frame, FrameCandidate, ProcessingRun, Track
    from app.db.session import SessionLocal

    project = _project("Thorough Source Delete")
    source = _source(project, tmp_path, "thorough")

    with SessionLocal() as db:
        run_ids = [r for r in db.scalars(select(ProcessingRun.id).where(ProcessingRun.source_id == source["id"]))]
        track_ids = [t for t in db.scalars(select(Track.id).where(Track.run_id.in_(run_ids)))]
        frame_ids = [f for f in db.scalars(select(Frame.id).where(Frame.source_id == source["id"]))]
        annotation_ids = [a for a in db.scalars(select(Annotation.id).where(Annotation.frame_id.in_(frame_ids)))]
        assert run_ids and track_ids and frame_ids and annotation_ids

    _delete(project, source)

    with SessionLocal() as db:
        left = {
            "processing_runs": db.query(ProcessingRun).filter(ProcessingRun.id.in_(run_ids)).count(),
            "tracks": db.query(Track).filter(Track.id.in_(track_ids)).count(),
            "frame_candidates": db.query(FrameCandidate).filter(FrameCandidate.track_id.in_(track_ids)).count(),
            "frames": db.query(Frame).filter(Frame.id.in_(frame_ids)).count(),
            "annotations": db.query(Annotation).filter(Annotation.id.in_(annotation_ids)).count(),
        }

    assert left == dict.fromkeys(left, 0), f"orphans left behind: {left}"


def test_another_source_in_the_same_project_is_untouched(tmp_path):
    project = _project("Two Sources")
    keep = _source(project, tmp_path / "keep", "keeper")
    doomed = _source(project, tmp_path / "doomed", "doomed")
    before = _contents(project, keep)

    _delete(project, doomed)

    assert _contents(project, keep) == before
    assert client.get(f"/projects/{project['id']}").status_code == 200


def test_the_projects_own_class_list_survives(tmp_path):
    """Classes belong to the project, not to whichever source happened
    to use them."""
    project = _project("Classes Survive")
    source = _source(project, tmp_path, "clip")

    _delete(project, source)

    assert len(client.get(f"/projects/{project['id']}/class-schema").json()) == 20


def test_the_sources_own_files_go(tmp_path):
    project = _project("Source Files")
    source = _source(project, tmp_path, "withfiles")
    workspace = Path(client.get(f"/projects/{project['id']}").json()["workspace_path"])
    copied_video = workspace / "source" / "withfiles.mp4"
    assert copied_video.is_file()

    body = _delete(project, source).json()

    assert body["files_removed"] is True
    assert not copied_video.exists()
    assert workspace.is_dir(), "the project's own workspace stays"


def test_an_exported_dataset_version_is_left_alone(tmp_path):
    """A version already exported is immutable and belongs to the
    project, not to one source. Deleting a source must not rewrite
    history that something may already have trained on."""
    project = _project("Export Survives")
    source = _source(project, tmp_path, "exported")
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert created.status_code == 201, created.text
    export_dir = Path(client.get(f"/projects/{project['id']}").json()["workspace_path"]) / "exports" / "v1"

    _delete(project, source)

    assert (export_dir / "manifest.json").is_file()
    assert len(client.get(f"/projects/{project['id']}/dataset-versions").json()) == 1


# --- what it refuses ----------------------------------------------------------


def test_a_source_with_work_running_is_not_deleted(tmp_path):
    project = _project("Busy Source")
    video = create_synthetic_video(tmp_path / "busy.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

    with deferred_jobs():
        submitted = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        )
        assert submitted.status_code == 202, submitted.text

    assert _contents(project, source)["running_jobs"] == 1

    response = _delete(project, source)

    assert response.status_code == 409
    assert response.json()["code"] == "source_busy"
    assert client.get(f"/projects/{project['id']}/sources/{source['id']}").status_code == 200


def test_a_live_capture_blocks_deletion(tmp_path):
    """The RTSP session has no job row at all - it starts capture
    threads and a running processing run, and writing into a source
    that is being deleted is the worst possible moment."""
    from app.db.models import ProcessingRun, Source
    from app.db.session import SessionLocal

    project = _project("Live Source")
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"],
            type="rtsp",
            path_or_uri="rtsp://camera/stream",
            fps=25.0,
            width=0,
            height=0,
            frame_count=0,
            duration_ms=0,
        )
        db.add(source)
        db.flush()
        source_id = source.id
        db.add(
            ProcessingRun(
                source_id=source_id,
                sampling_config={"protocol": "rtsp", "expected_fps": 25.0},
                status="running",
            )
        )
        db.commit()

    response = _delete(project, {"id": source_id})

    assert response.status_code == 409
    assert response.json()["code"] == "source_busy"


def test_deleting_a_source_from_the_wrong_project_is_a_404(tmp_path):
    """The project in the URL has to own the source, or a stale page
    could reach across projects."""
    mine = _project("Mine")
    theirs = _project("Theirs")
    source = _source(theirs, tmp_path, "theirs", process=False)

    response = _delete(mine, source)

    assert response.status_code == 404
    assert client.get(f"/projects/{theirs['id']}/sources/{source['id']}").status_code == 200


def test_deleting_an_unknown_source_is_a_404():
    project = _project("Unknown Source")

    assert client.delete(f"/projects/{project['id']}/sources/does-not-exist").status_code == 404
