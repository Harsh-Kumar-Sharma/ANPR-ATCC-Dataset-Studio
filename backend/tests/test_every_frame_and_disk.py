"""Keeping every frame that has a vehicle in it, without filling the
disk.

The sampling change is one parameter. The reason this took a ticket is
the disk: 9.4 GB free against a 90,003-frame video, where writing one
crop per vehicle per frame is gigabytes before anything has been
reviewed.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.models.frame_candidate import FrameCandidate
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app
from app.ml.types import Detection
from app.services import run_estimate
from app.services.run_estimate import estimate_run, frames_at
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _source(project: dict, tmp_path, frame_count: int = 20, fps: float = 10.0) -> dict:
    video = create_synthetic_video(
        tmp_path / "clip.mp4", frame_count=frame_count, fps=fps, width=WIDTH, height=HEIGHT
    )
    return client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()


# --- crops are no longer written ----------------------------------------------


def test_an_offline_run_writes_no_crops_at_all(tmp_path):
    """One JPEG per vehicle per frame is what made a full-rate pass
    impossible. The pixels are still in the video."""
    project = _project("No Crops Written")
    source = _source(project, tmp_path)
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)

    tracks_dir = Path(project["workspace_path"]) / "derived" / "tracks"
    written = list(tracks_dir.rglob("*.jpg")) if tracks_dir.exists() else []

    assert written == []


def test_the_crop_is_still_there_when_the_reviewer_asks_for_it(tmp_path):
    """Cut out of the source video on demand: the review UI cannot
    tell the difference, which is the point."""
    project = _project("Crop On Demand")
    source = _source(project, tmp_path)
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)

    tracks = client.get(f"/projects/{project['id']}/tracks").json()
    timeline = client.get(f"/tracks/{tracks[0]['id']}").json()
    candidate = timeline["frames"][0]

    response = client.get(f"/tracks/frames/{candidate['id']}/image")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert len(response.content) > 0


def test_the_crop_matches_the_box_it_was_recorded_with(tmp_path):
    """A crop cut from the wrong place is worse than no crop: it looks
    like a detection error."""
    project = _project("Crop Geometry")
    source = _source(project, tmp_path)
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)

    tracks = client.get(f"/projects/{project['id']}/tracks").json()
    candidate = client.get(f"/tracks/{tracks[0]['id']}").json()["frames"][0]

    import cv2
    import numpy as np

    response = client.get(f"/tracks/frames/{candidate['id']}/image")
    image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)

    x1, y1, x2, y2 = candidate["bbox_json"]
    assert image.shape[1] == round(x2) - round(x1)
    assert image.shape[0] == round(y2) - round(y1)


def test_a_candidate_row_no_longer_claims_a_file_that_is_not_there(tmp_path):
    """Null, rather than a path to a crop that was never written."""
    project = _project("No Phantom Paths")
    source = _source(project, tmp_path)
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)

    tracks = client.get(f"/projects/{project['id']}/tracks").json()
    with SessionLocal() as db:
        candidates = list(db.query(FrameCandidate).filter(FrameCandidate.track_id == tracks[0]["id"]))
        assert candidates
        assert all(c.image_path is None for c in candidates)


# --- running at the source's own rate -----------------------------------------


def test_the_estimate_counts_every_frame_in_that_mode(tmp_path):
    project = _project("Estimate Every Frame")
    source = _source(project, tmp_path, frame_count=20, fps=10.0)

    sampled = client.get(
        f"/projects/{project['id']}/sources/{source['id']}/process/estimate",
        params={"target_fps": 5.0},
    ).json()
    every = client.get(
        f"/projects/{project['id']}/sources/{source['id']}/process/estimate",
        params={"every_frame": True},
    ).json()

    assert every["frames_to_process"] == source["frame_count"]
    assert sampled["frames_to_process"] == source["frame_count"] // 2


def test_a_run_at_the_native_rate_keeps_every_frame_with_a_vehicle(tmp_path):
    project = _project("Native Rate Run")
    source = _source(project, tmp_path, frame_count=12, fps=10.0)

    submitted = process_source_sync(
        client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0, every_frame=True
    )

    run = client.get(f"/processing-runs/{submitted['run_id']}").json()
    indices = [f["frame_index"] for f in client.get(f"/projects/{project['id']}/frames").json()]

    # The run looked at every frame the source has, and the frames it
    # kept are consecutive - a sampled run would step over some. The
    # last frame of a synthetic clip does not always decode, so the
    # count is allowed to fall one short of the probe's.
    assert run["sampled_frame_count"] == source["frame_count"]
    assert len(indices) >= source["frame_count"] - 1
    assert indices == list(range(indices[0], indices[0] + len(indices)))


# --- the estimate -------------------------------------------------------------


def test_frames_at_a_target_rate_is_the_fraction_of_the_source():
    source = Source(project_id="p", type="video", path_or_uri="x.mp4", fps=30.0, width=1920, height=1080,
                    duration_ms=1000, frame_count=90_003)

    assert frames_at(source, target_fps=5.0, every_frame=False) == 15_000
    assert frames_at(source, target_fps=5.0, every_frame=True) == 90_003


def test_the_estimate_separates_what_the_run_writes_from_what_reviewing_costs(tmp_path):
    """The first number is small now. The second is the one that fills
    a disk, and hiding it would make the estimate useless."""
    source = Source(project_id="p", type="video", path_or_uri="x.mp4", fps=30.0, width=1920, height=1080,
                    duration_ms=1000, frame_count=90_003)

    estimate = estimate_run(source, tmp_path, every_frame=True)

    assert estimate.bytes_if_every_frame_reviewed > estimate.bytes_now * 100
    assert estimate.frames_to_process == 90_003


def test_a_run_that_will_not_fit_is_refused_before_it_starts(tmp_path, monkeypatch):
    """Not discovered at 80% with a half-written run to clean up."""
    project = _project("No Space")
    source = _source(project, tmp_path)
    monkeypatch.setattr(run_estimate, "free_bytes_for", lambda path: 1024)

    response = client.post(
        f"/projects/{project['id']}/sources/{source['id']}/process",
        json={"sampling_config": {"target_fps": 5.0}},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "not_enough_space"


def test_the_refusal_says_what_to_do_about_it(tmp_path, monkeypatch):
    project = _project("No Space Message")
    source = _source(project, tmp_path)
    monkeypatch.setattr(run_estimate, "free_bytes_for", lambda path: 1024)

    body = client.post(
        f"/projects/{project['id']}/sources/{source['id']}/process",
        json={"sampling_config": {"target_fps": 5.0}},
    ).json()

    assert "free" in body["message"].lower()
    assert "storage" in body["message"].lower()


# --- the budget, measured -----------------------------------------------------


def test_a_ninety_thousand_frame_run_stays_inside_a_stated_budget(tmp_path):
    """Asserted against the arithmetic rather than against a run: this
    is the estimate the refusal is made on, so it is the number that
    has to be right.

    The budget: a 90,003-frame pass writes rows, not pixels. Under
    100 MB while it runs is the claim the whole ticket rests on.
    """
    source = Source(project_id="p", type="video", path_or_uri="day_anpr_gantry.mp4", fps=30.0,
                    width=1920, height=1080, duration_ms=3_000_000, frame_count=90_003)

    estimate = estimate_run(source, tmp_path, every_frame=True)

    assert estimate.bytes_now < 100 * 1024**2, f"{estimate.bytes_now} bytes is over the budget"


def test_the_run_stops_cleanly_rather_than_filling_the_disk(tmp_path, monkeypatch):
    """The frames already found are real observations worth keeping."""
    from app.services import track_processor

    project = _project("Disk Runs Out")
    source = _source(project, tmp_path, frame_count=40, fps=10.0)

    monkeypatch.setattr(track_processor, "SPACE_CHECK_EVERY_FRAMES", 5)
    monkeypatch.setattr(track_processor, "free_bytes_for", lambda path: 0)

    submitted = process_source_sync(
        client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0, every_frame=True
    )

    run = client.get(f"/processing-runs/{submitted['run_id']}").json()
    assert run["status"] == "completed"
    assert run["sampled_frame_count"] < 40
    assert "disk" in (run["error_message"] or "").lower()
    # And it kept what it had found by then.
    assert client.get(f"/projects/{project['id']}/frames").json() != []


def test_progress_says_how_many_frames_were_kept(tmp_path):
    """"Frame 8000 of 90003" says nothing about whether the run is
    finding anything."""
    from app.services import track_processor

    said: list[str] = []

    project = _project("Progress Says Kept")
    source = _source(project, tmp_path, frame_count=20, fps=10.0)

    # Exercised through the service, because a job's progress crosses
    # a process boundary this test does not have.
    from app.db.models.processing_run import ProcessingRun
    from app.ml.bytetrack_tracker import ByteTrackTracker

    with SessionLocal() as db:
        source_row = db.get(Source, source["id"])
        run = ProcessingRun(source_id=source_row.id, sampling_config={"target_fps": 10.0}, status="running")
        db.add(run)
        db.commit()
        track_processor.process_source(
            db=db,
            source=source_row,
            workspace_path=Path(project["workspace_path"]),
            run=run,
            target_fps=10.0,
            detector=_OneCarDetector(),
            tracker=ByteTrackTracker(frame_rate=10.0),
            on_progress=lambda fraction, message: said.append(message),
        )

    assert any("kept" in message for message in said)


def test_the_row_cost_the_budget_rests_on_is_measured_not_guessed(tmp_path):
    """ROW_BYTES is the whole estimate. If it is wrong by an order of
    magnitude the refusal is wrong by an order of magnitude, so it is
    checked against what SQLite actually writes rather than against a
    memory of what it once wrote.

    A thousand rows, not ninety thousand: the cost is linear in rows
    and a smaller sample keeps the test fast. Generous bounds, because
    the point is the magnitude - page size, indexes and the free list
    all move the exact number around.
    """
    import sqlite3

    database = tmp_path / "rows.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE frame_candidates (
            id TEXT PRIMARY KEY, track_id TEXT, frame_id TEXT, frame_index INTEGER,
            timestamp_ms INTEGER, image_path TEXT, bbox_json TEXT, detector_class TEXT,
            detector_confidence REAL, blur_score REAL, sharpness_score REAL,
            area_ratio REAL, flags_json TEXT
        );
        CREATE INDEX ix_fc_track ON frame_candidates (track_id);
        CREATE INDEX ix_fc_frame ON frame_candidates (frame_id);
        """
    )
    rows = 1000
    connection.executemany(
        "INSERT INTO frame_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                f"{i:036d}",
                f"track-{i // 20:036d}",
                f"frame-{i:036d}",
                i,
                i * 33,
                None,
                "[100.0, 200.0, 340.5, 480.25]",
                "car",
                0.91,
                0.4,
                120.5,
                0.031,
                '{"truncated": false, "quality_score": 0.62, "roles": ["ocr_candidate"]}',
            )
            for i in range(rows)
        ],
    )
    connection.commit()
    connection.close()

    measured = database.stat().st_size / rows

    assert measured < run_estimate.ROW_BYTES * 2, (
        f"a row costs {measured:.0f} bytes, but the estimate assumes {run_estimate.ROW_BYTES}"
    )
    # And not wildly pessimistic either - an estimate ten times too
    # large would refuse runs that would have fitted.
    assert measured > run_estimate.ROW_BYTES / 10
