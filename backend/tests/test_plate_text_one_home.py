"""Ticket 15: a human's plate reading lives in one place.

It used to live in two. Typing a correction in track review added a
human-sourced row to ``ocr_candidates``; typing one on the labelling
canvas put it in the annotation. The two never compared equal, only one
of them exported, and the OCR agreement metric quietly counted the first
as evidence about the model.

The annotation is the one place. ``ocr_candidates`` holds what the model
read, and nothing else.
"""

import json
from pathlib import Path

import numpy as np
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.main import app
from app.ml.factory import get_default_ocr_engine
from app.ml.types import Detection, PlateOcrCandidate
from tests.job_execution import process_source_sync, tracks_for_run
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
CAR_CLASS = 4


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


class _StubOcrEngine:
    """Reads the same plate every time, at a chosen confidence."""

    def __init__(self, text: str = "MH12AB1234", confidence: float = 0.9) -> None:
        self.engine_version = "stub-plate-ocr-v1"
        self._text = text
        self._confidence = confidence

    def read_plate(self, vehicle_crop: np.ndarray) -> list[PlateOcrCandidate]:
        return [PlateOcrCandidate(bbox_xyxy=(1.0, 1.0, 9.0, 5.0), text=self._text, confidence=self._confidence)]


def _project_with_track(tmp_path, name: str):
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=8, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)
    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    return project, track


def _accept(track: dict, class_id: int = CAR_CLASS) -> dict:
    timeline = client.get(f"/tracks/{track['id']}").json()
    response = client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": timeline["frames"][0]["id"], "decision": "accepted", "class_id": class_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["annotation"]


def _run_ocr(track: dict, engine=None):
    app.dependency_overrides[get_default_ocr_engine] = lambda: engine or _StubOcrEngine()
    try:
        response = client.post(f"/tracks/{track['id']}/ocr")
        assert response.status_code == 200, response.text
        return response.json()
    finally:
        app.dependency_overrides.pop(get_default_ocr_engine, None)


def _set_plate(track: dict, text: str):
    return client.put(f"/tracks/{track['id']}/plate-text", json={"plate_text": text})


def _annotation(track: dict) -> dict:
    return client.get(f"/tracks/{track['id']}/annotation").json()


# --- one home ----------------------------------------------------------------


def test_a_corrected_plate_is_stored_on_the_annotation(tmp_path):
    """Not in a second table. The annotation is where a box's attributes
    live, and a plate is an attribute of the vehicle in the box."""
    project, track = _project_with_track(tmp_path, "Plate On Annotation")
    _accept(track)

    saved = _set_plate(track, "mh 12 ab 1234")
    assert saved.status_code == 200, saved.text

    assert _annotation(track)["attributes"]["plate_text"] == "MH12AB1234"


def test_correcting_a_plate_writes_no_human_row_to_the_ocr_table(tmp_path):
    """The table is the model's record now. A human row in it was the
    second home this ticket exists to close."""
    project, track = _project_with_track(tmp_path, "No Human Ocr Row")
    _accept(track)
    _run_ocr(track)

    _set_plate(track, "DL 3C 1234")

    candidates = client.get(f"/tracks/{track['id']}/ocr-candidates").json()
    assert candidates, "the model's own attempts stay"
    assert all(c["source"] == "model" for c in candidates)


def test_a_plate_cannot_be_recorded_before_there_is_a_label_to_put_it_on(tmp_path):
    """There is no annotation until the track is reviewed, and a plate
    with nowhere to go used to be written to a table that nothing
    exported. Refusing says so instead of losing it quietly."""
    project, track = _project_with_track(tmp_path, "No Annotation Yet")
    _run_ocr(track)

    refused = _set_plate(track, "MH 12 AB 1234")

    assert refused.status_code == 409
    assert refused.json()["code"] == "no_label_yet"
    assert "review" in refused.json()["message"].lower()


def test_a_plate_can_be_recorded_on_a_track_flagged_hard(tmp_path):
    """"Hard" is still a human decision that creates an annotation, and
    a difficult vehicle is exactly the one whose plate is worth keeping."""
    project, track = _project_with_track(tmp_path, "Hard Track Plate")
    timeline = client.get(f"/tracks/{track['id']}").json()
    client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": timeline["frames"][0]["id"], "decision": "hard", "class_id": CAR_CLASS},
    )

    saved = _set_plate(track, "KA 01 AA 1111")

    assert saved.status_code == 200, saved.text
    assert _annotation(track)["attributes"]["plate_text"] == "KA01AA1111"


def test_clearing_a_plate_removes_it(tmp_path):
    project, track = _project_with_track(tmp_path, "Clear Plate")
    _accept(track)
    _set_plate(track, "TN 09 BC 4321")

    cleared = _set_plate(track, "")

    assert cleared.status_code == 200, cleared.text
    assert "plate_text" not in _annotation(track)["attributes"]


def test_recording_a_plate_leaves_the_rest_of_the_label_alone(tmp_path):
    """It sets one attribute. The class, the box and anything else on the
    annotation are not its business."""
    project, track = _project_with_track(tmp_path, "Plate Leaves Rest")
    annotation = _accept(track, class_id=7)

    _set_plate(track, "UP 16 CD 0007")

    after = _annotation(track)
    assert after["id"] == annotation["id"]
    assert after["class_id"] == 7
    assert after["bbox_json"] == annotation["bbox_json"]


def test_an_impossible_plate_is_refused_the_same_way_the_canvas_refuses_it(tmp_path):
    """One validator, so the two UIs cannot disagree about what a plate
    may be."""
    project, track = _project_with_track(tmp_path, "Bad Plate")
    _accept(track)

    refused = _set_plate(track, "X" * 200)

    assert refused.status_code == 400
    assert "plate_text" in refused.json()["message"]


# --- what the model read, where a labeller can see it ------------------------


def test_a_frame_offers_the_plates_the_model_read_on_it(tmp_path):
    """So a labeller on the canvas is not retyping a plate the model
    already has. The canvas has no track, so it asks by frame."""
    project, track = _project_with_track(tmp_path, "Frame Plate Readings")
    annotation = _accept(track)
    _run_ocr(track)

    readings = client.get(f"/frames/{annotation['frame_id']}/plate-readings")
    assert readings.status_code == 200, readings.text

    body = readings.json()
    assert body, "the model read a plate on this frame"
    assert body[0]["normalized_text"] == "MH12AB1234"
    assert body[0]["confidence"] == 0.9
    assert body[0]["text"] == "MH12AB1234", "what it actually read, before canonicalising"


def test_a_frame_with_no_ocr_run_offers_nothing_rather_than_failing(tmp_path):
    project, track = _project_with_track(tmp_path, "No Readings")
    annotation = _accept(track)

    readings = client.get(f"/frames/{annotation['frame_id']}/plate-readings")

    assert readings.status_code == 200
    assert readings.json() == []


# --- the metric says what it measures ----------------------------------------


def test_the_agreement_metric_compares_the_model_against_the_humans_plate(tmp_path):
    project, track = _project_with_track(tmp_path, "Metric Agreement")
    _accept(track)
    _run_ocr(track)
    _set_plate(track, "MH12AB1234")  # exactly what the model read

    report = client.get(f"/processing-runs/{track['run_id']}/evaluation").json()

    assert report["ocr_metrics"]["tracks_with_ocr"] == 1
    assert report["ocr_metrics"]["agreements"] == 1
    assert report["ocr_metrics"]["corrections"] == 0
    assert report["ocr_metrics"]["agreement_rate"] == 1.0


def test_a_plate_the_human_changed_counts_as_a_correction(tmp_path):
    project, track = _project_with_track(tmp_path, "Metric Correction")
    _accept(track)
    _run_ocr(track)
    _set_plate(track, "DL 3C 9999")

    report = client.get(f"/processing-runs/{track['run_id']}/evaluation").json()

    assert report["ocr_metrics"]["tracks_with_ocr"] == 1
    assert report["ocr_metrics"]["agreements"] == 0
    assert report["ocr_metrics"]["corrections"] == 1


def test_a_track_nobody_has_read_is_not_counted_as_the_model_being_right(tmp_path):
    """The old metric marked the model's own best attempt as selected the
    moment OCR ran, so every track nobody looked at counted as an
    agreement - which made the rate a measure of how little reviewing had
    been done."""
    project, track = _project_with_track(tmp_path, "Metric Untouched")
    _accept(track)
    _run_ocr(track)

    report = client.get(f"/processing-runs/{track['run_id']}/evaluation").json()

    assert report["ocr_metrics"]["tracks_with_ocr"] == 0
    assert report["ocr_metrics"]["agreement_rate"] is None


# --- and out into the dataset ------------------------------------------------


def test_a_plate_recorded_in_track_review_reaches_the_exported_dataset(tmp_path):
    """The old human OCR row never left the database - export has no
    reference to the OCR table at all. That made the correction card a
    diary, which is the complaint ticket 13 made about not exporting
    attributes."""
    project, track = _project_with_track(tmp_path, "Plate Exports")
    _accept(track)
    _set_plate(track, "RJ 14 CV 0002")

    created = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert created.status_code == 201, created.text

    export_dir = Path(project["workspace_path"]) / "exports" / "v1"
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["objects"][0]["attributes"]["plate_text"] == "RJ14CV0002"


def test_a_plate_read_on_every_frame_of_a_track_is_one_suggestion(tmp_path):
    """Frames outlive runs, so re-running OCR piles more rows onto the
    same frame. The same plate read four times is one suggestion."""
    project, track = _project_with_track(tmp_path, "Deduped Readings")
    annotation = _accept(track)
    _run_ocr(track)
    _run_ocr(track)

    readings = client.get(f"/frames/{annotation['frame_id']}/plate-readings").json()

    assert len(readings) == 1, readings


def test_a_human_row_the_migration_left_behind_is_not_offered_as_the_models(tmp_path):
    """Those were written with confidence 1.0, so without a source
    filter somebody's own typing sorts to the top of a list headed
    "Model read:"."""
    from app.db.models import OcrCandidate
    from app.db.session import SessionLocal

    project, track = _project_with_track(tmp_path, "Orphan Human Row")
    annotation = _accept(track)
    _run_ocr(track)

    with SessionLocal() as db:
        candidate_id = db.scalar(
            select(OcrCandidate.frame_candidate_id).where(OcrCandidate.track_id == track["id"])
        )
        db.add(
            OcrCandidate(
                track_id=track["id"],
                frame_candidate_id=candidate_id,
                source="human",
                plate_bbox_json=[0.0, 0.0, 0.0, 0.0],
                text="TYPEDBYAPERSON",
                normalized_text="TYPEDBYAPERSON",
                confidence=1.0,
            )
        )
        db.commit()

    readings = client.get(f"/frames/{annotation['frame_id']}/plate-readings").json()

    assert [r["normalized_text"] for r in readings] == ["MH12AB1234"]


def test_running_ocr_twice_leaves_one_best_attempt(tmp_path):
    """The model's row says exactly one per track, and once human
    selection went away nothing was left to clear the previous run's."""
    from app.db.models import OcrCandidate
    from app.db.session import SessionLocal

    project, track = _project_with_track(tmp_path, "One Best Attempt")
    _accept(track)
    _run_ocr(track)
    _run_ocr(track)

    with SessionLocal() as db:
        selected = db.scalars(
            select(OcrCandidate).where(OcrCandidate.track_id == track["id"], OcrCandidate.selected.is_(True))
        ).all()

    assert len(selected) == 1


def test_a_request_that_forgets_the_field_does_not_wipe_a_plate(tmp_path):
    """A default would make a malformed request indistinguishable from a
    deliberate clear."""
    project, track = _project_with_track(tmp_path, "Missing Field")
    _accept(track)
    _set_plate(track, "MH 12 AB 1234")

    response = client.put(f"/tracks/{track['id']}/plate-text", json={})

    assert response.status_code == 422
    assert _annotation(track)["attributes"]["plate_text"] == "MH12AB1234"
