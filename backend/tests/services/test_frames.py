import pytest

from app.db.models import Annotation, Frame, FrameCandidate, ProcessingRun, Project, Source, Track
from app.db.session import SessionLocal
from app.services import class_definitions, frames
from app.services.frames import BoxInput, InvalidBoxError


def _project_with_frame(name: str, *, preset: str = "anpr-v1", frame_index: int = 0, video_path: str = "/nowhere.mp4"):
    """A project with one source and one 64x48 frame on it."""
    with SessionLocal() as db:
        project = Project(name=name, workspace_path=f"/workspace/{name}")
        db.add(project)
        db.flush()
        class_definitions.seed_project_classes(db, project.id, preset)
        source = Source(
            project_id=project.id,
            type="video",
            path_or_uri=video_path,
            fps=10.0,
            width=64,
            height=48,
            frame_count=10,
            duration_ms=1000,
        )
        db.add(source)
        db.flush()
        frame = Frame(source_id=source.id, frame_index=frame_index, timestamp_ms=frame_index * 100, width=64, height=48)
        db.add(frame)
        db.commit()
        db.refresh(project)
        db.refresh(source)
        db.refresh(frame)
        return project, source, frame


def _add_frame(source: Source, frame_index: int) -> Frame:
    with SessionLocal() as db:
        frame = Frame(source_id=source.id, frame_index=frame_index, timestamp_ms=frame_index * 100, width=64, height=48)
        db.add(frame)
        db.commit()
        db.refresh(frame)
        return frame


def _legacy_label(source: Source, frame: Frame, class_id: int, status: str = "accepted") -> tuple[str, str]:
    """A label written the old way, through track review, with the review
    decision ``status``. Returns (annotation_id, track_id)."""
    with SessionLocal() as db:
        run = ProcessingRun(source_id=source.id, sampling_config={"target_fps": 5.0}, status="completed")
        db.add(run)
        db.flush()
        track = Track(run_id=run.id, tracker_track_id=1, start_ts=0, end_ts=100, review_status=status)
        db.add(track)
        db.flush()
        candidate = FrameCandidate(
            track_id=track.id,
            frame_id=frame.id,
            frame_index=frame.frame_index,
            timestamp_ms=frame.timestamp_ms,
            image_path="/nowhere.jpg",
            bbox_json=[1, 1, 11, 11],
            detector_class="car",
            detector_confidence=0.9,
        )
        db.add(candidate)
        db.flush()
        annotation = Annotation(
            frame_id=frame.id,
            frame_candidate_id=candidate.id,
            source="human",
            class_id=class_id,
            bbox_json=[1, 1, 11, 11],
            status=status,
        )
        db.add(annotation)
        db.commit()
        return annotation.id, track.id


def _prediction(frame: Frame, class_id: int) -> str:
    """A box a model drew, not yet looked at by anyone."""
    with SessionLocal() as db:
        annotation = Annotation(
            frame_id=frame.id,
            frame_candidate_id=None,
            source="model",
            class_id=class_id,
            bbox_json=[5, 5, 15, 15],
            status="pending",
        )
        db.add(annotation)
        db.commit()
        return annotation.id


def _boxes_on(frame: Frame) -> list[list[float]]:
    with SessionLocal() as db:
        return [a.bbox_json for a in frames.list_annotations(db, frame.id)]


# --- the queue ---------------------------------------------------------------


def test_the_queue_lists_a_projects_frames_in_source_and_index_order():
    project, source, first = _project_with_frame("Queue Order", frame_index=5)
    _add_frame(source, 2)
    _add_frame(source, 9)

    with SessionLocal() as db:
        queue = frames.list_queue(db, project.id)

    assert [f.frame_index for f in queue] == [2, 5, 9]


def test_the_queue_is_scoped_to_the_project():
    mine, _, _ = _project_with_frame("Queue Scope A")
    theirs, _, _ = _project_with_frame("Queue Scope B")

    with SessionLocal() as db:
        assert {f.source_id for f in frames.list_queue(db, mine.id)} != {f.source_id for f in frames.list_queue(db, theirs.id)}
        assert len(frames.list_queue(db, mine.id)) == 1


def test_the_queue_can_be_narrowed_to_a_status():
    project, source, pending = _project_with_frame("Queue Status")
    done = _add_frame(source, 1)
    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, done.id), [])
        db.commit()

    with SessionLocal() as db:
        assert [f.id for f in frames.list_queue(db, project.id, status="pending")] == [pending.id]
        assert [f.id for f in frames.list_queue(db, project.id, status="labeled")] == [done.id]


# --- saving boxes --------------------------------------------------------------


def test_saving_boxes_stores_them_on_the_frame_and_marks_it_labeled():
    project, _, frame = _project_with_frame("Save Boxes")

    with SessionLocal() as db:
        saved = frames.replace_annotations(
            db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[2, 3, 20, 30])]
        )
        db.commit()
        assert len(saved) == 1

    assert _boxes_on(frame) == [[2, 3, 20, 30]]
    with SessionLocal() as db:
        stored = db.get(Frame, frame.id)
        assert stored.status == "labeled"
        annotation = frames.list_annotations(db, frame.id)[0]
        assert annotation.frame_id == frame.id
        assert annotation.frame_candidate_id is None, "a canvas box belongs to the frame, not to a detection"
        assert annotation.source == "human"
        assert annotation.class_id == 1


def test_saving_replaces_the_whole_set_so_a_removed_box_is_actually_gone():
    """The criterion that makes delete work without a delete endpoint."""
    project, _, frame = _project_with_frame("Replace Boxes")

    with SessionLocal() as db:
        frames.replace_annotations(
            db,
            project.id,
            db.get(Frame, frame.id),
            [BoxInput(class_id=1, bbox=[0, 0, 10, 10]), BoxInput(class_id=2, bbox=[20, 20, 30, 30])],
        )
        db.commit()
    assert len(_boxes_on(frame)) == 2

    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=2, bbox=[20, 20, 30, 30])])
        db.commit()

    assert _boxes_on(frame) == [[20, 20, 30, 30]]


def test_saving_no_boxes_is_a_label_that_means_nothing_here():
    """An empty frame is a real, valuable label - it teaches "no vehicle".
    It must read as labelled, not as still waiting."""
    project, _, frame = _project_with_frame("Empty Label")

    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [])
        db.commit()

    assert _boxes_on(frame) == []
    with SessionLocal() as db:
        assert db.get(Frame, frame.id).status == "labeled"


def test_many_boxes_on_one_frame_is_the_whole_point():
    project, _, frame = _project_with_frame("Many Boxes")

    with SessionLocal() as db:
        frames.replace_annotations(
            db,
            project.id,
            db.get(Frame, frame.id),
            [BoxInput(class_id=1, bbox=[0, 0, 10, 10]), BoxInput(class_id=1, bbox=[12, 0, 22, 10]), BoxInput(class_id=2, bbox=[30, 30, 40, 40])],
        )
        db.commit()

    assert len(_boxes_on(frame)) == 3


def test_a_box_may_have_no_class_yet():
    """The class picker is a later ticket. Until then a drawn box is still
    worth keeping."""
    project, _, frame = _project_with_frame("Classless Box")

    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=None, bbox=[0, 0, 10, 10])])
        db.commit()
        assert frames.list_annotations(db, frame.id)[0].class_id is None


def test_attributes_round_trip():
    project, _, frame = _project_with_frame("Attributes")

    with SessionLocal() as db:
        frames.replace_annotations(
            db,
            project.id,
            db.get(Frame, frame.id),
            [BoxInput(class_id=2, bbox=[0, 0, 10, 10], attributes={"plate_text": "KA05MN1234", "night": True})],
        )
        db.commit()

    with SessionLocal() as db:
        assert frames.list_annotations(db, frame.id)[0].attributes == {"plate_text": "KA05MN1234", "night": True}


# --- validation --------------------------------------------------------------


@pytest.mark.parametrize(
    "bbox, why",
    [
        ([-1, 0, 10, 10], "left of the frame"),
        ([0, 0, 65, 10], "past the right edge"),
        ([0, 0, 10, 49], "below the bottom edge"),
        ([10, 10, 10, 20], "zero width"),
        ([10, 10, 20, 10], "zero height"),
        ([20, 20, 10, 30], "inverted x"),
        ([0, 0, 10], "wrong length"),
    ],
)
def test_a_box_that_could_not_be_on_this_frame_is_refused(bbox, why):
    project, _, frame = _project_with_frame(f"Bad Box {why}")

    with SessionLocal() as db:
        with pytest.raises(InvalidBoxError):
            frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=bbox)])


def test_a_box_on_the_frame_edge_is_fine():
    project, _, frame = _project_with_frame("Edge Box")

    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[0, 0, 64, 48])])
        db.commit()

    assert _boxes_on(frame) == [[0, 0, 64, 48]]


def test_a_box_with_a_class_the_project_does_not_have_is_refused():
    project, _, frame = _project_with_frame("Bad Class")

    with SessionLocal() as db:
        with pytest.raises(InvalidBoxError):
            frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=99, bbox=[0, 0, 10, 10])])


def test_one_bad_box_rejects_the_whole_save():
    """Saving is all-or-nothing: the previous set must survive a failed
    save, or a typo in one box silently wipes the rest."""
    project, _, frame = _project_with_frame("Atomic Save")
    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[0, 0, 10, 10])])
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(InvalidBoxError):
            frames.replace_annotations(
                db,
                project.id,
                db.get(Frame, frame.id),
                [BoxInput(class_id=1, bbox=[20, 20, 30, 30]), BoxInput(class_id=1, bbox=[0, 0, 999, 10])],
            )
        db.rollback()

    assert _boxes_on(frame) == [[0, 0, 10, 10]]


# --- the legacy path meets the canvas ----------------------------------------


def test_a_track_review_label_shows_up_as_a_box_on_its_frame():
    _, source, frame = _project_with_frame("Legacy Visible")
    _legacy_label(source, frame, class_id=2)

    assert _boxes_on(frame) == [[1, 1, 11, 11]]


def test_a_canvas_save_that_drops_a_legacy_box_removes_it_and_resets_its_track():
    """The canvas is the truth for its frame. A box it no longer shows is
    gone, and the track that box reviewed cannot keep claiming a review
    that no longer exists - the same rule deleting a class established."""
    project, source, frame = _project_with_frame("Legacy Replaced")
    annotation_id, track_id = _legacy_label(source, frame, class_id=2)

    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[30, 30, 40, 40])])
        db.commit()

    with SessionLocal() as db:
        assert db.get(Annotation, annotation_id) is None
        assert db.get(Track, track_id).review_status == "unreviewed"
    assert _boxes_on(frame) == [[30, 30, 40, 40]]


# --- the image -----------------------------------------------------------------


def test_the_frame_image_is_decoded_on_demand_and_then_cached(tmp_path):
    from tests.video_factory import create_synthetic_video

    video = create_synthetic_video(tmp_path / "frames.mp4", frame_count=10, fps=10.0)
    project, _, frame = _project_with_frame("Frame Image", frame_index=3, video_path=str(video))
    with SessionLocal() as db:
        db.get(Project, project.id).workspace_path = str(tmp_path / "ws")
        db.commit()

    with SessionLocal() as db:
        path = frames.frame_image_path(db, db.get(Frame, frame.id))
        db.commit()

    assert path.is_file()
    assert path.suffix == ".jpg"
    with SessionLocal() as db:
        assert db.get(Frame, frame.id).image_path == str(path), "the decoded file is remembered, not re-decoded"


def test_a_missing_source_video_is_a_clear_error_not_a_crash():
    from app.services.frame_materializer import FrameMaterializationError

    project, _, frame = _project_with_frame("Missing Video", video_path="/definitely/not/here.mp4")

    with SessionLocal() as db:
        with pytest.raises(FrameMaterializationError):
            frames.frame_image_path(db, db.get(Frame, frame.id))


# --- a box the canvas loaded stays itself ---------------------------------------


def test_resaving_unchanged_boxes_keeps_their_ids():
    """Delete-and-reinsert would churn every id on every save."""
    project, _, frame = _project_with_frame("Stable Ids")
    with SessionLocal() as db:
        first = frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[0, 0, 10, 10])])
        db.commit()
        original_id = first[0].id

    with SessionLocal() as db:
        again = frames.replace_annotations(
            db, project.id, db.get(Frame, frame.id), [BoxInput(id=original_id, class_id=1, bbox=[0, 0, 10, 10])]
        )
        ids_after = [a.id for a in again]
        db.commit()

    assert ids_after == [original_id]


def test_an_echoed_box_is_updated_in_place():
    project, _, frame = _project_with_frame("Update In Place")
    with SessionLocal() as db:
        saved = frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[0, 0, 10, 10])])
        db.commit()
        box_id = saved[0].id

    with SessionLocal() as db:
        frames.replace_annotations(
            db, project.id, db.get(Frame, frame.id), [BoxInput(id=box_id, class_id=2, bbox=[5, 5, 20, 20], attributes={"moved": True})]
        )
        db.commit()

    with SessionLocal() as db:
        updated = db.get(Annotation, box_id)
        assert updated.class_id == 2
        assert updated.bbox_json == [5, 5, 20, 20]
        assert updated.attributes == {"moved": True}


def test_resaving_an_exported_box_keeps_its_dataset_item():
    """The bug the review found: every save deleted and re-inserted, and
    deleting cascades dataset items - so re-saving a frame with an
    exported box silently dropped that box from the version's index."""
    from app.db.models import DatasetItem, DatasetVersion

    project, _, frame = _project_with_frame("Exported Box Survives")
    with SessionLocal() as db:
        saved = frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[0, 0, 10, 10])])
        db.commit()
        box_id = saved[0].id
        version = DatasetVersion(project_id=project.id, version=1, split_seed=1, config_snapshot_json={})
        db.add(version)
        db.flush()
        db.add(DatasetItem(dataset_version_id=version.id, annotation_id=box_id, split="train", export_path="x.jpg"))
        db.commit()

    with SessionLocal() as db:
        frames.replace_annotations(
            db, project.id, db.get(Frame, frame.id), [BoxInput(id=box_id, class_id=1, bbox=[0, 0, 10, 10])]
        )
        db.commit()

    with SessionLocal() as db:
        assert db.query(DatasetItem).filter(DatasetItem.annotation_id == box_id).count() == 1


def test_an_echoed_legacy_box_keeps_its_candidate_and_its_tracks_review():
    """Echoing a track-review box back means "keep it" - nothing about it
    should change, least of all the track's review state."""
    project, source, frame = _project_with_frame("Legacy Kept")
    annotation_id, track_id = _legacy_label(source, frame, class_id=2)

    with SessionLocal() as db:
        frames.replace_annotations(
            db,
            project.id,
            db.get(Frame, frame.id),
            [BoxInput(id=annotation_id, class_id=2, bbox=[1, 1, 11, 11]), BoxInput(class_id=1, bbox=[30, 30, 40, 40])],
        )
        db.commit()

    with SessionLocal() as db:
        kept = db.get(Annotation, annotation_id)
        assert kept is not None
        assert kept.frame_candidate_id is not None
        assert db.get(Track, track_id).review_status == "accepted"


def test_echoing_a_box_that_is_no_longer_there_is_refused():
    """The canvas's view is stale. Guessing whether the user meant to keep
    it is worse than asking them to reload."""
    project, _, frame = _project_with_frame("Stale Box")
    with SessionLocal() as db:
        saved = frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[0, 0, 10, 10])])
        db.commit()
        box_id = saved[0].id
    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [])
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(frames.StaleBoxError):
            frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(id=box_id, class_id=1, bbox=[0, 0, 10, 10])])


def test_sending_the_same_box_twice_is_refused():
    project, _, frame = _project_with_frame("Duplicate Echo")
    with SessionLocal() as db:
        saved = frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[0, 0, 10, 10])])
        db.commit()
        box_id = saved[0].id

    with SessionLocal() as db:
        with pytest.raises(InvalidBoxError):
            frames.replace_annotations(
                db,
                project.id,
                db.get(Frame, frame.id),
                [BoxInput(id=box_id, class_id=1, bbox=[0, 0, 10, 10]), BoxInput(id=box_id, class_id=2, bbox=[20, 20, 30, 30])],
            )


# --- the queue only offers frames that can be opened -----------------------------


def test_a_live_session_frame_with_no_image_is_kept_out_of_the_queue():
    """An RTSP source has no video file to decode from. Without a stored
    image there is nothing to show, and the queue entry would error on
    click."""
    project, source, _ = _project_with_frame("RTSP Queue")
    with SessionLocal() as db:
        db.get(Source, source.id).type = "rtsp"
        db.commit()

    with SessionLocal() as db:
        assert frames.list_queue(db, project.id) == []


def test_a_live_session_frame_whose_image_was_stored_is_offered():
    project, source, frame = _project_with_frame("RTSP Stored Image")
    with SessionLocal() as db:
        db.get(Source, source.id).type = "rtsp"
        db.get(Frame, frame.id).image_path = "/somewhere/frame.jpg"
        db.commit()

    with SessionLocal() as db:
        assert [f.id for f in frames.list_queue(db, project.id)] == [frame.id]


def test_an_unknown_status_filter_is_refused_not_an_empty_queue():
    project, _, _ = _project_with_frame("Bad Status Filter")

    with SessionLocal() as db:
        with pytest.raises(frames.UnknownFrameStatusError):
            frames.list_queue(db, project.id, status="done")


def test_one_call_can_keep_one_box_by_id_and_drop_another():
    """The echo contract in a single save, which is what the canvas really
    sends: a box sent back with its id stays that row, a box left out is
    gone."""
    project, _, frame = _project_with_frame("Mixed Echo")
    with SessionLocal() as db:
        saved = frames.replace_annotations(
            db,
            project.id,
            db.get(Frame, frame.id),
            [BoxInput(class_id=1, bbox=[0, 0, 10, 10]), BoxInput(class_id=2, bbox=[20, 20, 30, 30])],
        )
        keep_id, drop_id = saved[0].id, saved[1].id
        db.commit()

    with SessionLocal() as db:
        again = frames.replace_annotations(
            db, project.id, db.get(Frame, frame.id), [BoxInput(id=keep_id, class_id=1, bbox=[0, 0, 10, 10])]
        )
        ids_after = [a.id for a in again]
        db.commit()

    assert ids_after == [keep_id]
    with SessionLocal() as db:
        assert db.get(Annotation, drop_id) is None


# --- the set includes what a model drew --------------------------------------------


def test_a_prediction_sent_back_becomes_the_humans_box():
    """Pre-annotation is the compounding step: the model draws, the human
    keeps or fixes. Keeping is just echoing the box - and from then on it
    is the human's truth, not a prediction."""
    project, _, frame = _project_with_frame("Confirm Prediction")
    predicted = _prediction(frame, class_id=2)

    with SessionLocal() as db:
        frames.replace_annotations(
            db, project.id, db.get(Frame, frame.id), [BoxInput(id=predicted, class_id=2, bbox=[5, 5, 15, 15])]
        )
        db.commit()

    with SessionLocal() as db:
        confirmed = db.get(Annotation, predicted)
        assert confirmed.source == "human"
        assert confirmed.status == "accepted"


def test_a_prediction_left_out_is_rejected_and_removed():
    project, _, frame = _project_with_frame("Reject Prediction")
    predicted = _prediction(frame, class_id=2)

    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[30, 30, 40, 40])])
        db.commit()

    with SessionLocal() as db:
        assert db.get(Annotation, predicted) is None


def test_editing_a_reviewed_box_keeps_the_decision_it_was_given():
    """A track reviewed as failed, then nudged on the canvas, is still a
    failed review. Moving a box is not re-reviewing the track."""
    project, source, frame = _project_with_frame("Keep Decision")
    annotation_id, track_id = _legacy_label(source, frame, class_id=2, status="failed")

    with SessionLocal() as db:
        frames.replace_annotations(
            db, project.id, db.get(Frame, frame.id), [BoxInput(id=annotation_id, class_id=2, bbox=[2, 2, 12, 12])]
        )
        db.commit()

    with SessionLocal() as db:
        edited = db.get(Annotation, annotation_id)
        assert edited.bbox_json == [2, 2, 12, 12]
        assert edited.status == "failed"
        assert db.get(Track, track_id).review_status == "failed"


# --- rejecting a frame ---------------------------------------------------------


def test_rejecting_a_frame_takes_it_out_of_the_queue():
    project, source, first = _project_with_frame("Reject Queue")
    second = _add_frame(source, 1)

    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, first.id), "rejected")
        db.commit()

    with SessionLocal() as db:
        assert [f.id for f in frames.list_queue(db, project.id)] == [second.id]
        # Still reachable when asked for by name, so it can be undone.
        assert [f.id for f in frames.list_queue(db, project.id, status="rejected")] == [first.id]


def test_a_rejected_frame_can_be_put_back():
    project, _, frame = _project_with_frame("Unreject")
    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, frame.id), "rejected")
        db.commit()

    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, frame.id), "pending")
        db.commit()

    with SessionLocal() as db:
        assert [f.id for f in frames.list_queue(db, project.id)] == [frame.id]


def test_rejecting_a_frame_keeps_the_boxes_already_on_it():
    """Rejecting says "not worth labelling", not "destroy my work" - the
    frame can be put back."""
    project, _, frame = _project_with_frame("Reject Keeps Boxes")
    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[0, 0, 10, 10])])
        db.commit()

    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, frame.id), "rejected")
        db.commit()

    assert len(_boxes_on(frame)) == 1


def test_an_unknown_status_cannot_be_set():
    project, _, frame = _project_with_frame("Bad Status Set")

    with SessionLocal() as db:
        with pytest.raises(frames.UnknownFrameStatusError):
            frames.set_status(db, db.get(Frame, frame.id), "done")


def test_progress_counts_what_has_been_done():
    project, source, pending = _project_with_frame("Progress Counts")
    labeled = _add_frame(source, 1)
    rejected = _add_frame(source, 2)
    _add_frame(source, 3)

    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, labeled.id), [])
        frames.set_status(db, db.get(Frame, rejected.id), "rejected")
        db.commit()

    with SessionLocal() as db:
        progress = frames.queue_progress(db, project.id)

    assert progress == {"pending": 2, "labeled": 1, "rejected": 1, "skipped": 0, "total": 4}


def test_progress_is_scoped_to_the_project():
    mine, _, _ = _project_with_frame("Progress Scope A")
    _project_with_frame("Progress Scope B")

    with SessionLocal() as db:
        assert frames.queue_progress(db, mine.id)["total"] == 1


def test_putting_a_labelled_frame_back_remembers_it_was_labelled():
    """Status has to say what was actually done. Label, skip, un-skip used
    to leave a frame full of boxes reporting "pending", which also made
    the progress counts wrong."""
    project, _, frame = _project_with_frame("Unreject Keeps Labelled")
    with SessionLocal() as db:
        frames.replace_annotations(db, project.id, db.get(Frame, frame.id), [BoxInput(class_id=1, bbox=[0, 0, 10, 10])])
        frames.set_status(db, db.get(Frame, frame.id), "rejected")
        db.commit()

    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, frame.id), "pending")
        db.commit()

    with SessionLocal() as db:
        assert db.get(Frame, frame.id).status == "labeled"
        assert frames.queue_progress(db, project.id) == {"pending": 0, "labeled": 1, "rejected": 0, "skipped": 0, "total": 1}


def test_putting_an_untouched_frame_back_leaves_it_pending():
    project, _, frame = _project_with_frame("Unreject Stays Pending")
    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, frame.id), "rejected")
        db.commit()

    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, frame.id), "pending")
        db.commit()

    with SessionLocal() as db:
        assert db.get(Frame, frame.id).status == "pending"


# --- ticket 11: frames the machine set aside ------------------------------------


def test_a_frame_selection_passed_over_is_not_offered():
    project, source, kept = _project_with_frame("Selection Queue")
    passed_over = _add_frame(source, 1)

    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, passed_over.id), "skipped")
        db.commit()

    with SessionLocal() as db:
        assert [f.id for f in frames.list_queue(db, project.id)] == [kept.id]
        assert [f.id for f in frames.list_queue(db, project.id, status="skipped")] == [passed_over.id]


def test_progress_counts_what_selection_set_aside_separately_from_what_a_human_did():
    """"3 skipped" should not lump together "I looked and said no" with
    "the machine never offered it"."""
    project, source, _ = _project_with_frame("Selection Progress")
    machine = _add_frame(source, 1)
    human = _add_frame(source, 2)

    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, machine.id), "skipped")
        frames.set_status(db, db.get(Frame, human.id), "rejected")
        db.commit()

    with SessionLocal() as db:
        assert frames.queue_progress(db, project.id) == {
            "pending": 1,
            "labeled": 0,
            "rejected": 1,
            "skipped": 1,
            "total": 3,
        }


def test_a_frame_selection_passed_over_can_still_be_put_back():
    project, _, frame = _project_with_frame("Selection Put Back")
    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, frame.id), "skipped")
        db.commit()

    with SessionLocal() as db:
        frames.set_status(db, db.get(Frame, frame.id), "pending")
        db.commit()

    with SessionLocal() as db:
        assert [f.id for f in frames.list_queue(db, project.id)] == [frame.id]
