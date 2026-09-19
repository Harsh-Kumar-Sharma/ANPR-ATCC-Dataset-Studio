"""Deleting a project, and everything that has to be true first.

This is the most destructive thing the app can do: a project owns
sources, frames, tracks, every label drawn on them, the dataset versions
exported from them and a workspace directory that can run to gigabytes.
None of it comes back.

So most of these tests are about what deletion *refuses* to do.
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


def _project(tmp_path, name: str, *, with_work: bool = True) -> dict:
    """A project, optionally with a source processed and a frame labelled."""
    project = client.post("/projects", json={"name": name}).json()
    if not with_work:
        return project

    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)

    frames = client.get(f"/projects/{project['id']}/frames").json()
    saved = client.put(
        f"/frames/{frames[0]['id']}/annotations",
        json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
    )
    assert saved.status_code == 200, saved.text
    return project


def _contents(project: dict) -> dict:
    response = client.get(f"/projects/{project['id']}/contents")
    assert response.status_code == 200, response.text
    return response.json()


def _delete(project: dict, confirm: str | None = None):
    return client.request(
        "DELETE",
        f"/projects/{project['id']}",
        json={"name": project["name"] if confirm is None else confirm},
    )


# --- what is in there ---------------------------------------------------------


def test_the_contents_say_what_deleting_would_destroy(tmp_path):
    """A confirmation that cannot say what is about to go is not a
    confirmation."""
    project = _project(tmp_path, "Contents Project")

    contents = _contents(project)

    assert contents["sources"] == 1
    assert contents["frames"] > 0
    assert contents["tracks"] == 1
    assert contents["labels"] == 1
    assert contents["dataset_versions"] == 0
    assert contents["workspace_bytes"] > 0, "the frames and crops on disk are the bulk of it"
    assert contents["running_jobs"] == 0


def test_an_empty_project_reports_an_empty_project(tmp_path):
    project = _project(tmp_path, "Empty Contents", with_work=False)

    contents = _contents(project)

    assert contents["sources"] == 0
    assert contents["frames"] == 0
    assert contents["labels"] == 0


def test_the_contents_count_exported_dataset_versions(tmp_path):
    project = _project(tmp_path, "Exported Contents")
    created = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert created.status_code == 201, created.text

    assert _contents(project)["dataset_versions"] == 1


# --- deleting -----------------------------------------------------------------


def test_deleting_a_project_removes_it(tmp_path):
    project = _project(tmp_path, "Deleted Project")

    response = _delete(project)

    assert response.status_code == 200, response.text
    assert client.get(f"/projects/{project['id']}").status_code == 404
    assert project["id"] not in [p["id"] for p in client.get("/projects").json()]


def test_deleting_reports_what_it_destroyed(tmp_path):
    """So the confirmation the user saw and the outcome can be compared."""
    project = _project(tmp_path, "Reporting Delete")
    before = _contents(project)

    removed = _delete(project).json()

    assert removed["sources"] == before["sources"]
    assert removed["frames"] == before["frames"]
    assert removed["labels"] == before["labels"]


def test_everything_the_project_owned_goes_with_it(tmp_path):
    """Nothing enforces the foreign keys here, so orphans do not
    announce themselves - they just sit there being counted by the next
    query that forgets to scope itself.

    The ids are captured *before* the delete. An earlier version of this
    test looked them up afterwards, by which point the sources were
    already gone, so every query ran against an empty id list and passed
    whatever the cascade did or did not do. Removing five of the twelve
    delete statements left it green.
    """
    from app.db.models import (
        Annotation,
        ClassDefinition,
        DatasetItem,
        DatasetVersion,
        Frame,
        FrameCandidate,
        OcrCandidate,
        ProcessingRun,
        Source,
        Track,
    )
    from app.db.session import SessionLocal

    project = _project(tmp_path, "Thorough Delete")
    exported = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert exported.status_code == 201, exported.text

    with SessionLocal() as db:
        source_ids = [s for s in db.scalars(select(Source.id).where(Source.project_id == project["id"]))]
        run_ids = [r for r in db.scalars(select(ProcessingRun.id).where(ProcessingRun.source_id.in_(source_ids)))]
        track_ids = [t for t in db.scalars(select(Track.id).where(Track.run_id.in_(run_ids)))]
        frame_ids = [f for f in db.scalars(select(Frame.id).where(Frame.source_id.in_(source_ids)))]
        annotation_ids = [a for a in db.scalars(select(Annotation.id).where(Annotation.frame_id.in_(frame_ids)))]
        version_ids = [
            v for v in db.scalars(select(DatasetVersion.id).where(DatasetVersion.project_id == project["id"]))
        ]
        assert source_ids and run_ids and track_ids and frame_ids and annotation_ids and version_ids

    _delete(project)

    with SessionLocal() as db:
        left = {
            "sources": db.query(Source).filter(Source.id.in_(source_ids)).count(),
            "processing_runs": db.query(ProcessingRun).filter(ProcessingRun.id.in_(run_ids)).count(),
            "tracks": db.query(Track).filter(Track.id.in_(track_ids)).count(),
            "frame_candidates": db.query(FrameCandidate).filter(FrameCandidate.track_id.in_(track_ids)).count(),
            "ocr_candidates": db.query(OcrCandidate).filter(OcrCandidate.track_id.in_(track_ids)).count(),
            "frames": db.query(Frame).filter(Frame.id.in_(frame_ids)).count(),
            "annotations": db.query(Annotation).filter(Annotation.id.in_(annotation_ids)).count(),
            "dataset_versions": db.query(DatasetVersion).filter(DatasetVersion.id.in_(version_ids)).count(),
            "dataset_items": db.query(DatasetItem)
            .filter(DatasetItem.dataset_version_id.in_(version_ids))
            .count(),
            "class_definitions": db.query(ClassDefinition)
            .filter(ClassDefinition.project_id == project["id"])
            .count(),
        }

    assert left == dict.fromkeys(left, 0), f"orphans left behind: {left}"


def test_an_ocr_reading_goes_with_its_track(tmp_path):
    """Reached only through track ids, so it is the row most easily
    forgotten - and a stray one points at a track that no longer exists."""
    from app.db.models import FrameCandidate, OcrCandidate
    from app.db.session import SessionLocal

    from app.db.models import ProcessingRun, Source, Track

    project = _project(tmp_path, "Ocr Delete")
    with SessionLocal() as db:
        # This project's own candidate, not whichever one happens to be
        # first in a database shared with every other test in the file.
        candidate = db.scalars(
            select(FrameCandidate)
            .join(Track, FrameCandidate.track_id == Track.id)
            .join(ProcessingRun, Track.run_id == ProcessingRun.id)
            .join(Source, ProcessingRun.source_id == Source.id)
            .where(Source.project_id == project["id"])
        ).first()
        assert candidate is not None
        reading_id = OcrCandidate(
            track_id=candidate.track_id,
            frame_candidate_id=candidate.id,
            source="model",
            plate_bbox_json=[0.0, 0.0, 10.0, 5.0],
            text="MH12AB1234",
            normalized_text="MH12AB1234",
            confidence=0.9,
        )
        db.add(reading_id)
        db.commit()
        reading_id = reading_id.id

    _delete(project)

    with SessionLocal() as db:
        assert db.get(OcrCandidate, reading_id) is None


def test_another_projects_work_is_untouched(tmp_path):
    """The only thing worse than not being able to delete a project."""
    keep = _project(tmp_path / "keep", "Survivor Project")
    doomed = _project(tmp_path / "doomed", "Doomed Project")
    before = _contents(keep)

    _delete(doomed)

    assert _contents(keep) == before
    assert client.get(f"/projects/{keep['id']}").status_code == 200


def test_the_workspace_directory_goes_too(tmp_path):
    """Frames, track crops and exports live there, and they are the
    gigabytes. Leaving them is how a disk fills up with projects nobody
    can name any more."""
    project = _project(tmp_path, "Workspace Delete")
    workspace = Path(project["workspace_path"])
    assert workspace.is_dir()

    _delete(project)

    assert not workspace.exists()


# --- what it refuses ----------------------------------------------------------


def test_the_wrong_name_deletes_nothing(tmp_path):
    """Typing the name is the interlock. A mis-aimed request - the wrong
    id in a URL, a stale page - should not be able to destroy a project
    the user was not looking at."""
    project = _project(tmp_path, "Interlock Project")

    response = _delete(project, confirm="Something Else")

    assert response.status_code == 409
    assert response.json()["code"] == "name_mismatch"
    assert client.get(f"/projects/{project['id']}").status_code == 200
    assert _contents(project)["labels"] == 1


def test_the_name_must_match_exactly(tmp_path):
    project = _project(tmp_path, "Case Project", with_work=False)

    assert _delete(project, confirm="case project").status_code == 409
    assert _delete(project, confirm=" Case Project ").status_code == 409
    assert _delete(project, confirm="Case Project").status_code == 200


def test_a_project_with_work_running_is_not_deleted(tmp_path):
    """A detached worker is still writing to these rows and to that
    directory. Deleting underneath it leaves a process filling a
    workspace that no longer belongs to anything."""
    project = client.post("/projects", json={"name": "Busy Project"}).json()
    video = create_synthetic_video(tmp_path / "busy.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

    with deferred_jobs():
        submitted = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        )
        assert submitted.status_code == 202, submitted.text

    assert _contents(project)["running_jobs"] == 1

    response = _delete(project)

    assert response.status_code == 409
    assert response.json()["code"] == "project_busy"
    assert "job" in response.json()["message"].lower()
    assert client.get(f"/projects/{project['id']}").status_code == 200


def test_deleting_an_unknown_project_is_a_404(tmp_path):
    response = client.request("DELETE", "/projects/does-not-exist", json={"name": "whatever"})

    assert response.status_code == 404


def test_a_workspace_path_outside_the_workspace_root_is_left_alone(tmp_path):
    """The recorded path is a column. A recursive delete driven by a
    column is a recursive delete driven by whatever wrote it, so the
    project's rows go and the directory is reported rather than
    removed."""
    from app.db.models import Project
    from app.db.session import SessionLocal

    project = _project(tmp_path, "Escaped Workspace", with_work=False)
    elsewhere = tmp_path / "not-the-workspace"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("not ours to delete", encoding="utf-8")

    with SessionLocal() as db:
        db.get(Project, project["id"]).workspace_path = str(elsewhere)
        db.commit()

    response = _delete(project)

    assert response.status_code == 200, response.text
    assert response.json()["workspace_removed"] is False
    assert (elsewhere / "keep.txt").is_file(), "a path outside the workspace root is not ours to delete"
    assert client.get(f"/projects/{project['id']}").status_code == 404


def test_a_live_capture_blocks_deletion_even_though_it_has_no_job(tmp_path, monkeypatch):
    """An RTSP session starts capture threads and a `running` run, and
    never creates a job row. A job-only check let the project be deleted
    out from under a camera that was still writing into its workspace."""
    from app.db.models import ProcessingRun, Source
    from app.db.session import SessionLocal

    project = _project(tmp_path, "Live Capture", with_work=False)
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"],
            type="rtsp",
            path_or_uri="rtsp://camera/stream",
            fps=25.0,
            width=1920,
            height=1080,
            frame_count=0,
            duration_ms=0,
        )
        db.add(source)
        db.flush()
        db.add(
            ProcessingRun(
                source_id=source.id,
                sampling_config={"protocol": "rtsp", "expected_fps": 25.0},
                status="running",
            )
        )
        db.commit()

    assert _contents(project)["running_jobs"] == 1

    response = _delete(project)

    assert response.status_code == 409
    assert response.json()["code"] == "project_busy"
    assert client.get(f"/projects/{project['id']}").status_code == 200


def test_a_finished_detection_run_does_not_block_deletion(tmp_path):
    """Only live captures count. A detect job creates a run of its own,
    so counting every unfinished run as well would report two things in
    flight for one piece of work - and a completed run must not block
    anything at all."""
    project = _project(tmp_path, "Finished Runs")

    assert _contents(project)["running_jobs"] == 0
    assert _delete(project).status_code == 200


def test_the_refusal_does_not_hand_back_the_name(tmp_path):
    """The interlock is meant to stop a mis-aimed request, not only a
    typo. Repeating the name in the 409 lets any client that retries
    read it out of the refusal and resend."""
    project = _project(tmp_path, "Secret Name", with_work=False)

    body = _delete(project, confirm="wrong").json()

    assert "Secret Name" not in body["message"]


def test_a_directory_without_our_manifest_is_not_deleted(tmp_path):
    """The recorded path passing a name check is not the same as the
    directory being one this app made. `project.json` is what makes the
    answer about the directory itself rather than about the column."""
    from app.db.models import Project
    from app.db.session import SessionLocal

    project = _project(tmp_path, "No Manifest", with_work=False)
    workspace = Path(project["workspace_path"])
    (workspace / "project.json").unlink()
    (workspace / "someone-elses-work.txt").write_text("not ours", encoding="utf-8")

    response = _delete(project)

    assert response.status_code == 200, response.text
    assert response.json()["workspace_removed"] is False
    assert (workspace / "someone-elses-work.txt").is_file()
    with SessionLocal() as db:
        assert db.get(Project, project["id"]) is None


def test_a_manifest_naming_another_project_is_not_deleted(tmp_path):
    """A workspace restored from another machine, or a directory reused
    by hand. The name matches; the manifest says otherwise."""
    import json as json_module

    project = _project(tmp_path, "Wrong Manifest", with_work=False)
    workspace = Path(project["workspace_path"])
    (workspace / "project.json").write_text(
        json_module.dumps({"project_id": "some-other-project", "name": "Someone Else"}), encoding="utf-8"
    )

    response = _delete(project)

    assert response.json()["workspace_removed"] is False
    assert workspace.is_dir()


def test_the_happy_path_reports_the_workspace_as_removed(tmp_path):
    """Only the refusal cases were asserted, so "it said true" was never
    checked against "it is actually gone"."""
    project = _project(tmp_path, "Reported Removed")
    workspace = Path(project["workspace_path"])

    body = _delete(project).json()

    assert body["workspace_removed"] is True
    assert not workspace.exists()


def test_a_project_larger_than_sqlites_parameter_cap_can_still_be_deleted(tmp_path):
    """Gathering ids one bound parameter per frame ran into SQLite's
    32 766 cap while *building* the annotation list, so a big enough
    project could not be deleted at all - a 500 and "An unexpected error
    occurred"."""
    from app.db.models import Frame, Source
    from app.db.session import SessionLocal

    project = _project(tmp_path, "Very Large", with_work=False)
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"],
            type="video",
            path_or_uri=str(tmp_path / "large.mp4"),
            fps=25.0,
            width=64,
            height=48,
            frame_count=40_000,
            duration_ms=1_600_000,
        )
        db.add(source)
        db.flush()
        source_id = source.id
        db.add_all(
            Frame(source_id=source_id, frame_index=i, timestamp_ms=i * 40, width=64, height=48)
            for i in range(40_000)
        )
        db.commit()

    assert _contents(project)["frames"] == 40_000

    response = _delete(project)

    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.query(Frame).filter(Frame.source_id == source_id).count() == 0
