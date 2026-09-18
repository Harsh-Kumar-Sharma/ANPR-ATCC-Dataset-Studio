import pytest

from app.core.errors import ConflictError, NotFoundError
from app.services.class_definitions import InvalidClassNameError, InvalidRemapError
from app.db.models import Annotation, ClassDefinition, FrameCandidate, Frame, ProcessingRun, Project, Source, Track
from app.db.session import SessionLocal
from app.services import class_definitions
from app.services.dataset_query import project_annotations


def _project(name: str) -> Project:
    with SessionLocal() as db:
        project = Project(name=name, workspace_path=f"/workspace/{name}")
        db.add(project)
        db.commit()
        class_definitions.seed_project_classes(db, project.id, "anpr-v1")
        db.commit()
        db.refresh(project)
        return project


def _label_with(project: Project, class_id: int, status: str = "accepted") -> str:
    """Put one human annotation on this project using ``class_id``."""
    with SessionLocal() as db:
        source = Source(
            project_id=project.id,
            type="video",
            path_or_uri="/nowhere.mp4",
            fps=10.0,
            width=64,
            height=48,
            frame_count=10,
            duration_ms=1000,
        )
        db.add(source)
        db.flush()
        run = ProcessingRun(source_id=source.id, sampling_config={"target_fps": 5.0}, status="completed")
        db.add(run)
        db.flush()
        track = Track(run_id=run.id, tracker_track_id=1, start_ts=0, end_ts=100)
        db.add(track)
        db.flush()
        frame = Frame(source_id=source.id, frame_index=0, timestamp_ms=0, width=64, height=48)
        db.add(frame)
        db.flush()
        candidate = FrameCandidate(
            track_id=track.id,
            frame_id=frame.id,
            frame_index=0,
            timestamp_ms=0,
            image_path="/nowhere.jpg",
            bbox_json=[0, 0, 10, 10],
            detector_class="car",
            detector_confidence=0.9,
        )
        db.add(candidate)
        db.flush()
        annotation = Annotation(
            frame_candidate_id=candidate.id,
            source="human",
            class_id=class_id,
            bbox_json=[0, 0, 10, 10],
            status=status,
        )
        db.add(annotation)
        db.commit()
        return annotation.id


def test_adding_a_class_appends_it_to_the_project():
    project = _project("Add Class")

    with SessionLocal() as db:
        created = class_definitions.create_class(db, project.id, "truck")
        db.commit()
        class_id = created.class_id

    with SessionLocal() as db:
        names = [c.name for c in class_definitions.list_project_classes(db, project.id)]

    assert names == ["vehicle", "number_plate", "truck"]
    assert class_id == 3, "a new class takes the next id, after the highest in use"


def test_an_id_is_only_ever_reused_once_no_label_can_mean_it():
    """Ids are what labels point at, so reissuing one is only safe if
    nothing still holds it - which deletion already guarantees, because
    it refuses while a class is in use. This pins that pairing: the id
    does come back, and it is only allowed to because the class was
    provably unused when it went."""
    project = _project("Id Reuse After Unused")

    with SessionLocal() as db:
        first = class_definitions.create_class(db, project.id, "truck")
        db.commit()
        first_id = first.class_id

    # Deletion goes through the service, which is where the guarantee is.
    with SessionLocal() as db:
        assert class_definitions.count_labels_using(db, project.id, first_id) == 0
        class_definitions.delete_class(db, project.id, first_id)
        db.commit()

    with SessionLocal() as db:
        second = class_definitions.create_class(db, project.id, "bus")
        db.commit()

        assert second.class_id == first_id
        assert class_definitions.count_labels_using(db, project.id, first_id) == 0


def test_renaming_a_class_touches_no_labels():
    """The property that makes renaming always safe."""
    project = _project("Rename Safe")
    annotation_id = _label_with(project, class_id=2)

    with SessionLocal() as db:
        class_definitions.rename_class(db, project.id, 2, "plate")
        db.commit()

    with SessionLocal() as db:
        assert db.get(Annotation, annotation_id).class_id == 2
        names = {c.class_id: c.name for c in class_definitions.list_project_classes(db, project.id)}
        assert names[2] == "plate"


def test_two_classes_cannot_share_a_name():
    project = _project("Duplicate Name")

    with SessionLocal() as db:
        with pytest.raises(ConflictError):
            class_definitions.create_class(db, project.id, "vehicle")


def test_renaming_onto_another_classes_name_is_refused():
    project = _project("Rename Collision")

    with SessionLocal() as db:
        with pytest.raises(ConflictError):
            class_definitions.rename_class(db, project.id, 2, "vehicle")


def test_renaming_a_class_to_its_own_name_is_not_a_collision():
    project = _project("Rename Noop")

    with SessionLocal() as db:
        renamed = class_definitions.rename_class(db, project.id, 1, "vehicle")
        assert renamed.name == "vehicle"


def test_names_are_compared_without_case_or_padding():
    """'Vehicle' and 'vehicle ' are the same class to whoever is labelling,
    and two of them would quietly split a dataset in half."""
    project = _project("Name Normalisation")

    with SessionLocal() as db:
        with pytest.raises(ConflictError):
            class_definitions.create_class(db, project.id, "  Vehicle ")


def test_a_blank_name_is_refused():
    project = _project("Blank Name")

    with SessionLocal() as db:
        with pytest.raises(InvalidClassNameError):
            class_definitions.create_class(db, project.id, "   ")


def test_a_name_is_stored_trimmed():
    project = _project("Trimmed Name")

    with SessionLocal() as db:
        created = class_definitions.create_class(db, project.id, "  truck  ")
        db.commit()
        assert created.name == "truck"


def test_renaming_a_class_that_does_not_exist_is_a_not_found():
    project = _project("Rename Missing")

    with SessionLocal() as db:
        with pytest.raises(NotFoundError):
            class_definitions.rename_class(db, project.id, 99, "whatever")


def test_counting_how_many_labels_use_a_class():
    """Ticket 07 asks the user "these 47 labels use this class" - this is
    where the 47 comes from."""
    project = _project("Label Count")
    _label_with(project, class_id=2)
    _label_with(project, class_id=2)
    _label_with(project, class_id=1)

    with SessionLocal() as db:
        assert class_definitions.count_labels_using(db, project.id, 2) == 2
        assert class_definitions.count_labels_using(db, project.id, 1) == 1


def test_a_class_in_use_cannot_be_deleted_yet():
    """Deleting in use needs the remap conversation, which is ticket 07.
    Until then it refuses rather than orphaning labels."""
    project = _project("Delete In Use")
    _label_with(project, class_id=2)

    with SessionLocal() as db:
        with pytest.raises(ConflictError):
            class_definitions.delete_class(db, project.id, 2)

    with SessionLocal() as db:
        assert class_definitions.is_valid_class_id(db, project.id, 2) is True


def test_an_unused_class_can_be_deleted():
    project = _project("Delete Unused")

    with SessionLocal() as db:
        class_definitions.delete_class(db, project.id, 2)
        db.commit()

    with SessionLocal() as db:
        assert [c.name for c in class_definitions.list_project_classes(db, project.id)] == ["vehicle"]


def test_labels_in_another_project_do_not_protect_this_ones_class():
    """Counts must be scoped to the project, or an unrelated project's
    labels would make a class undeletable here."""
    first = _project("Scoped Counts A")
    second = _project("Scoped Counts B")
    _label_with(first, class_id=2)

    with SessionLocal() as db:
        assert class_definitions.count_labels_using(db, second.id, 2) == 0
        class_definitions.delete_class(db, second.id, 2)
        db.commit()

    with SessionLocal() as db:
        assert class_definitions.is_valid_class_id(db, first.id, 2) is True


def test_duplicate_detection_is_not_fooled_by_non_ascii_case():
    """SQLite's lower() only touches A-Z, so comparing in SQL let an
    accented case-variant through - and the table's own uniqueness
    constraint is case-sensitive, so nothing caught it either."""
    project = _project("Unicode Names")

    with SessionLocal() as db:
        class_definitions.create_class(db, project.id, "véhicule")
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(ConflictError):
            class_definitions.create_class(db, project.id, "VÉHICULE")


def test_duplicate_detection_handles_other_unicode_casing_rules():
    project = _project("Unicode Casing")

    with SessionLocal() as db:
        class_definitions.create_class(db, project.id, "Straße")
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(ConflictError):
            class_definitions.create_class(db, project.id, "STRASSE")


def test_a_rejected_review_is_reported_as_what_it_is():
    """A rejected review still carries a class_id, so it still blocks the
    delete - but telling the user "1 label uses this" when they cannot
    find that label anywhere is not an honest answer."""
    project = _project("Rejected Blocks Delete")
    _label_with(project, class_id=2, status="failed")

    with SessionLocal() as db:
        assert class_definitions.count_labels_using(db, project.id, 2) == 1
        with pytest.raises(ConflictError) as raised:
            class_definitions.delete_class(db, project.id, 2)

    assert "reject" in str(raised.value).lower()


def test_duplicate_detection_when_the_stored_name_is_the_uppercase_one():
    """The order matters: SQLite's lower() leaves an accented capital
    alone, so a stored 'VÉHICULE' never matched an incoming 'véhicule'."""
    project = _project("Unicode Stored Upper")

    with SessionLocal() as db:
        class_definitions.create_class(db, project.id, "VÉHICULE")
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(ConflictError):
            class_definitions.create_class(db, project.id, "véhicule")


# --- ticket 07: deleting a class that is in use ----------------------------


def _track_of(annotation_id: str) -> Track:
    with SessionLocal() as db:
        annotation = db.get(Annotation, annotation_id)
        candidate = db.get(FrameCandidate, annotation.frame_candidate_id)
        return db.get(Track, candidate.track_id)


def test_remapping_moves_every_label_to_the_target_and_removes_the_class():
    project = _project("Remap Labels")
    first = _label_with(project, class_id=2)
    second = _label_with(project, class_id=2)
    untouched = _label_with(project, class_id=1)

    with SessionLocal() as db:
        outcome = class_definitions.delete_class(db, project.id, 2, remap_to=1)
        db.commit()

    assert outcome.remapped == 2
    assert outcome.deleted_labels == 0
    with SessionLocal() as db:
        assert db.get(Annotation, first).class_id == 1
        assert db.get(Annotation, second).class_id == 1
        assert db.get(Annotation, untouched).class_id == 1
        assert [c.name for c in class_definitions.list_project_classes(db, project.id)] == ["vehicle"]


def test_merging_two_classes_is_a_remap():
    """"Merge B into A" and "delete B, moving its labels to A" are the
    same operation. There is deliberately only one path."""
    project = _project("Merge Classes")
    label = _label_with(project, class_id=1)

    with SessionLocal() as db:
        class_definitions.delete_class(db, project.id, 1, remap_to=2)
        db.commit()

    with SessionLocal() as db:
        assert db.get(Annotation, label).class_id == 2
        assert class_definitions.is_valid_class_id(db, project.id, 1) is False


def test_deleting_the_labels_removes_them_with_the_class():
    project = _project("Delete Labels")
    doomed = _label_with(project, class_id=2)
    survivor = _label_with(project, class_id=1)

    with SessionLocal() as db:
        outcome = class_definitions.delete_class(db, project.id, 2, delete_labels=True)
        db.commit()

    assert outcome.deleted_labels == 1
    assert outcome.remapped == 0
    with SessionLocal() as db:
        assert db.get(Annotation, doomed) is None
        assert db.get(Annotation, survivor) is not None
        assert class_definitions.is_valid_class_id(db, project.id, 2) is False


def test_a_track_whose_label_was_deleted_no_longer_claims_to_be_reviewed():
    """The track's review_status was set when the label was written. With
    the label gone, "accepted" would be a review that no longer exists."""
    project = _project("Track Reset")
    doomed = _label_with(project, class_id=2)
    track_id = _track_of(doomed).id
    with SessionLocal() as db:
        db.get(Track, track_id).review_status = "accepted"
        db.commit()

    with SessionLocal() as db:
        class_definitions.delete_class(db, project.id, 2, delete_labels=True)
        db.commit()

    with SessionLocal() as db:
        assert db.get(Track, track_id).review_status == "unreviewed"


def test_deleting_a_label_takes_its_dataset_item_with_it():
    """SQLite is not enforcing foreign keys here, so a dataset item left
    pointing at a deleted annotation would sit there silently. The export
    on disk is the durable record of what a version contained; the item
    row is only an index into it."""
    from app.db.models import DatasetItem, DatasetVersion

    project = _project("Dataset Item Cascade")
    doomed = _label_with(project, class_id=2)
    with SessionLocal() as db:
        version = DatasetVersion(project_id=project.id, version=1, split_seed=1, config_snapshot_json={})
        db.add(version)
        db.flush()
        db.add(DatasetItem(dataset_version_id=version.id, annotation_id=doomed, split="train", export_path="x.jpg"))
        db.commit()
        version_id = version.id

    with SessionLocal() as db:
        class_definitions.delete_class(db, project.id, 2, delete_labels=True)
        db.commit()

    with SessionLocal() as db:
        assert db.query(DatasetItem).filter(DatasetItem.annotation_id == doomed).count() == 0
        assert db.get(DatasetVersion, version_id) is not None, "the version itself is a record and stays"


def test_no_label_references_a_class_that_no_longer_exists_after_either_path():
    """The property the whole ticket exists for."""
    project = _project("No Orphans")
    _label_with(project, class_id=1)
    _label_with(project, class_id=2)
    with SessionLocal() as db:
        class_definitions.create_class(db, project.id, "truck")
        db.commit()
    _label_with(project, class_id=3)

    with SessionLocal() as db:
        class_definitions.delete_class(db, project.id, 2, remap_to=1)
        class_definitions.delete_class(db, project.id, 3, delete_labels=True)
        db.commit()

    with SessionLocal() as db:
        live_ids = {c.class_id for c in class_definitions.list_project_classes(db, project.id)}
        referenced = {
            a.class_id
            for a in db.scalars(project_annotations(project.id).where(Annotation.class_id.is_not(None)))
        }
        assert referenced <= live_ids


def test_remapping_onto_the_class_being_deleted_is_refused():
    project = _project("Remap To Self")
    _label_with(project, class_id=2)

    with SessionLocal() as db:
        with pytest.raises(InvalidRemapError):
            class_definitions.delete_class(db, project.id, 2, remap_to=2)


def test_remapping_onto_a_class_that_does_not_exist_is_refused():
    """Remapping onto nothing would orphan every label it touched - the
    exact outcome this ticket exists to prevent."""
    project = _project("Remap To Missing")
    _label_with(project, class_id=2)

    with SessionLocal() as db:
        with pytest.raises(NotFoundError):
            class_definitions.delete_class(db, project.id, 2, remap_to=99)

    with SessionLocal() as db:
        assert class_definitions.count_labels_using(db, project.id, 2) == 1


def test_asking_for_both_remap_and_delete_is_refused():
    project = _project("Both Options")
    _label_with(project, class_id=2)

    with SessionLocal() as db:
        with pytest.raises(InvalidRemapError):
            class_definitions.delete_class(db, project.id, 2, remap_to=1, delete_labels=True)


def test_remapping_is_scoped_to_the_project():
    first = _project("Remap Scope A")
    second = _project("Remap Scope B")
    theirs = _label_with(second, class_id=2)
    _label_with(first, class_id=2)

    with SessionLocal() as db:
        class_definitions.delete_class(db, first.id, 2, remap_to=1)
        db.commit()

    with SessionLocal() as db:
        assert db.get(Annotation, theirs).class_id == 2
        assert class_definitions.is_valid_class_id(db, second.id, 2) is True
