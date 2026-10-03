"""What must never end up in a dataset, and what must never go quiet.

Ticket 12 gave the export a second way for a frame to qualify: a human
labelled it and it holds nothing, so it ships as a background image with
an empty label file. That is good training data when it is true and
poison when it is not, and "is it true" turned out to be easy to get
wrong. Every test here is a way it was wrong.

The helpers come from the canvas export tests rather than being copied -
the setup is identical and two copies would drift.
"""

from tests.test_dataset_export_from_canvas import (  # noqa: F401
    CAR,
    HEIGHT,
    TRUCK,
    WIDTH,
    _draw,
    _export,
    _project_with_queue,
    _SteadyDetector,
    client,
)
from tests.job_execution import process_source_sync, tracks_for_run
from tests.video_factory import create_synthetic_video


def test_a_hard_box_is_not_a_frame_with_nothing_in_it(tmp_path):
    """Review said "this vehicle is difficult". The canvas leaves that
    decision alone but marks the frame labelled, so the frame reads as
    labelled with no *accepted* box on it. Calling that "nothing here"
    turns "this one is hard" into a training image asserting the vehicle
    is not there."""
    project = client.post("/projects", json={"name": "Hard Not Empty"}).json()
    video = create_synthetic_video(tmp_path / "hard.mp4", frame_count=12, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    submitted = process_source_sync(client, project["id"], source["id"], _SteadyDetector(), target_fps=10.0)

    track = tracks_for_run(client, project["id"], submitted["run_id"])[0]
    timeline = client.get(f"/tracks/{track['id']}").json()
    reviewed = client.put(
        f"/tracks/{track['id']}/review",
        json={"frame_candidate_id": timeline["frames"][0]["id"], "decision": "hard", "class_id": 4},
    )
    assert reviewed.status_code == 200, reviewed.text
    hard_frame_id = reviewed.json()["annotation"]["frame_id"]

    # The frame is still offered by the queue - reviewing a track does
    # not take it out - so opening it and saving is an ordinary thing to
    # do, and it is what makes the frame read as labelled.
    queue = client.get(f"/projects/{project['id']}/frames").json()
    existing = client.get(f"/frames/{hard_frame_id}/annotations").json()
    kept = client.put(
        f"/frames/{hard_frame_id}/annotations",
        json={
            "annotations": [
                {"id": a["id"], "class_id": a["class_id"], "bbox_json": a["bbox_json"], "attributes": a["attributes"]}
                for a in existing
            ]
        },
    )
    assert kept.status_code == 200, kept.text
    assert client.get(f"/frames/{hard_frame_id}").json()["status"] == "labeled"

    # Something else has to be exportable, or the export refuses outright
    # and the test proves nothing.
    other = next(f for f in queue if f["id"] != hard_frame_id)
    _draw(other, [(4, CAR)])

    _, manifest = _export(project)

    exported = {item["frame_id"] for item in manifest["items"]}
    assert hard_frame_id not in exported, "a frame holding a hard box was exported as an empty background image"
    assert manifest["background_frames"] == 0


def test_deleting_a_class_does_not_turn_its_frames_into_background(tmp_path):
    """Deleting a class with its labels empties the frames that used it.
    If those frames keep saying "labelled", the export reads them as a
    human declaring the picture empty - and ships the very vehicles that
    were just unlabelled as background."""
    project, queue = _project_with_queue(tmp_path, "Deleted Class")
    _draw(queue[0], [(4, CAR), (7, TRUCK)])
    _draw(queue[1], [(7, TRUCK)])

    deleted = client.delete(f"/projects/{project['id']}/classes/7", params={"delete_labels": True})
    assert deleted.status_code == 200, deleted.text

    _, manifest = _export(project)

    by_frame = {item["frame_id"]: item for item in manifest["items"]}
    assert len(by_frame[queue[0]["id"]]["objects"]) == 1, "the surviving box should still export"
    assert queue[1]["id"] not in by_frame, "an emptied frame is not a background image"
    assert manifest["background_frames"] == 0


def test_emptying_a_frame_by_deleting_its_class_puts_it_back_in_the_queue(tmp_path):
    """The frame's only box is gone, so there is nothing labelled about
    it any more. Saying otherwise both misreports it in the queue and is
    what let the export call it background."""
    project, queue = _project_with_queue(tmp_path, "Emptied By Delete")
    _draw(queue[0], [(7, TRUCK)])
    assert client.get(f"/frames/{queue[0]['id']}").json()["status"] == "labeled"

    deleted = client.delete(f"/projects/{project['id']}/classes/7", params={"delete_labels": True})
    assert deleted.status_code == 200, deleted.text

    assert client.get(f"/frames/{queue[0]['id']}").json()["status"] == "pending"


def test_a_frame_that_keeps_a_box_stays_labelled(tmp_path):
    """The other half of the same rule: only a frame with nothing human
    left on it goes back to pending."""
    project, queue = _project_with_queue(tmp_path, "Keeps A Box")
    _draw(queue[0], [(4, CAR), (7, TRUCK)])

    client.delete(f"/projects/{project['id']}/classes/7", params={"delete_labels": True})

    assert client.get(f"/frames/{queue[0]['id']}").json()["status"] == "labeled"


def test_an_unclassified_box_is_reported_rather_than_quietly_dropped(tmp_path):
    """One classified box and one the user never got round to. The frame
    exports - the classified box is real work - but the other box is not
    in the label file, and the object it marks ships as background. That
    is a loss, and the export has to say so."""
    project, queue = _project_with_queue(tmp_path, "Half Classified")
    saved = client.put(
        f"/frames/{queue[0]['id']}/annotations",
        json={"annotations": [{"class_id": 4, "bbox_json": CAR}, {"class_id": None, "bbox_json": TRUCK}]},
    )
    assert saved.status_code == 200, saved.text

    created, manifest = _export(project)

    item = manifest["items"][0]
    assert len(item["objects"]) == 1
    assert item["unclassified_box_count"] == 1
    assert manifest["frames_with_unclassified_boxes"] == 1
    assert created["frames_with_unclassified_boxes"] == 1
    assert any("no class" in w for w in created["validation"]["warnings"]), created["validation"]["warnings"]


def test_a_half_finished_frame_still_gets_the_partial_label_warning(tmp_path):
    """The warning is suppressed for a frame a human *finished*. A frame
    with a box they never classified is the opposite of finished, and
    suppressing it there hid exactly the case it exists for."""
    detector = _SteadyDetector(boxes=[(5.0, 5.0, 45.0, 35.0), (60.0, 50.0, 110.0, 85.0)])
    project, queue = _project_with_queue(tmp_path, "Half Finished", detector=detector)
    client.put(
        f"/frames/{queue[0]['id']}/annotations",
        json={"annotations": [{"class_id": 4, "bbox_json": CAR}, {"class_id": None, "bbox_json": TRUCK}]},
    )

    created, manifest = _export(project)

    assert manifest["partially_labeled_frames"] == 1
    assert manifest["items"][0]["unlabeled_detection_count"] == 1
    assert any("no accepted annotation" in w for w in created["validation"]["warnings"])


def test_claiming_a_frame_is_empty_is_surfaced_rather_than_taken_on_trust(tmp_path):
    """Saving no boxes on a frame the detector found vehicles in is a
    strong claim to make over the detector's head. It is allowed - the
    human is the truth - but it is not passed in silence."""
    detector = _SteadyDetector(boxes=[(5.0, 5.0, 45.0, 35.0), (60.0, 50.0, 110.0, 85.0)])
    project, queue = _project_with_queue(tmp_path, "Empty Over Detector", detector=detector)
    _draw(queue[0], [(4, CAR)])
    _draw(queue[1], [])

    created, manifest = _export(project)

    background = next(item for item in manifest["items"] if item["frame_id"] == queue[1]["id"])
    assert background["objects"] == []
    assert background["unlabeled_detection_count"] == 2
    assert manifest["partially_labeled_frames"] == 1
    assert created["validation"]["valid"] is True, "it is a warning, not an error - the human decides"


def test_a_frame_whose_boxes_all_lack_a_class_is_not_background(tmp_path):
    """Unfinished work, not an empty picture. It stays out of the
    dataset, and the export says how much it left behind rather than
    shrinking in silence."""
    project, queue = _project_with_queue(tmp_path, "None Classified")
    client.put(f"/frames/{queue[0]['id']}/annotations", json={"annotations": [{"class_id": None, "bbox_json": CAR}]})
    _draw(queue[1], [(4, CAR)])

    created, manifest = _export(project)

    assert [item["frame_id"] for item in manifest["items"]] == [queue[1]["id"]]
    assert manifest["background_frames"] == 0
    assert manifest["frames_skipped_unclassified"] == 1
    assert created["frames_skipped_unclassified"] == 1
    assert any("left out of this dataset" in w for w in created["validation"]["warnings"])


def test_an_export_with_nothing_to_write_fails_rather_than_reporting_success(tmp_path):
    """A dataset version with no images in it is not a dataset. It used
    to be created anyway, consume a version number, and come back as
    "validation passed"."""
    project, queue = _project_with_queue(tmp_path, "Nothing Writable")
    client.put(f"/frames/{queue[0]['id']}/annotations", json={"annotations": [{"class_id": None, "bbox_json": CAR}]})

    response = client.post(f"/projects/{project['id']}/dataset-versions", json={})

    assert response.status_code == 400
    assert response.json()["code"] == "nothing_to_export"
    assert "class" in response.json()["message"]
    assert client.get(f"/projects/{project['id']}/dataset-versions").json() == []


def test_the_split_is_computed_over_what_is_actually_written(tmp_path):
    """Ratios have to describe the dataset that lands, not the set the
    query returned. Splitting twenty frames and then writing ten of them
    gives neither 80/10/10 nor anything anyone asked for."""
    project, queue = _project_with_queue(tmp_path, "Split Over Written", frame_count=24)
    assert len(queue) >= 20
    for position, frame in enumerate(queue[:20]):
        client.put(
            f"/frames/{frame['id']}/annotations",
            json={"annotations": [{"class_id": 4 if position % 2 == 0 else None, "bbox_json": CAR}]},
        )

    _, manifest = _export(project, split_seed=7)

    # 75/15/10 of the ten actually written.
    assert manifest["counts"] == {"train": 8, "val": 2, "test": 0, "total": 10}


def test_a_dataset_that_is_mostly_background_says_so(tmp_path):
    """The point of counting background frames was being able to see a
    dataset made of nothing. A number in a one-off response could not do
    that; re-validating an export on disk has to reach the same verdict."""
    project, queue = _project_with_queue(tmp_path, "Mostly Background", frame_count=16)
    _draw(queue[0], [(4, CAR)])
    for frame in queue[1:6]:
        _draw(frame, [])

    created, manifest = _export(project)

    assert manifest["background_frames"] == 5
    assert manifest["counts"]["total"] == 6
    assert any("trains a detector to find nothing" in w for w in created["validation"]["warnings"])

    version_id = created["dataset_version"]["id"]
    revalidated = client.get(f"/dataset-versions/{version_id}/validate").json()
    assert any("trains a detector to find nothing" in w for w in revalidated["warnings"])


def test_too_few_passages_falls_back_to_frames_and_says_so(tmp_path):
    """One short clip is one passage: splitting by passage would put
    everything in train. It splits by frame instead, and says plainly
    that validation now shares vehicles with training."""
    project, queue = _project_with_queue(tmp_path, "One Passage", frame_count=12)
    for frame in queue[:10]:
        client.put(f"/frames/{frame['id']}/annotations", json={"annotations": [{"class_id": 4, "bbox_json": CAR}]})

    created, manifest = _export(project, split_seed=3)

    assert manifest["config"]["split_unit"] == "frame"
    assert manifest["config"]["passages"] == 1
    assert any("too few to keep each vehicle in one split" in w for w in created["validation"]["warnings"])
