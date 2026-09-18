import pytest

from app.core.presets import ANPR_V1, ATCC_V1, get_preset
from app.db.models import ClassDefinition, Project
from app.db.session import SessionLocal
from app.services import class_definitions


def _project(name: str) -> Project:
    with SessionLocal() as db:
        project = Project(name=name, workspace_path=f"/workspace/{name}")
        db.add(project)
        db.commit()
        db.refresh(project)
        return project


def test_seeding_copies_a_preset_into_the_project():
    project = _project("Seeded ATCC")

    with SessionLocal() as db:
        class_definitions.seed_project_classes(db, project.id, "atcc-v1")

    with SessionLocal() as db:
        classes = class_definitions.list_project_classes(db, project.id)

    assert [c.name for c in classes] == [p.name for p in ATCC_V1]
    assert [c.class_id for c in classes] == [p.class_id for p in ATCC_V1]


def test_the_anpr_preset_is_the_two_classes_a_plate_detector_needs():
    project = _project("Seeded ANPR")

    with SessionLocal() as db:
        class_definitions.seed_project_classes(db, project.id, "anpr-v1")
        names = [c.name for c in class_definitions.list_project_classes(db, project.id)]

    assert names == [p.name for p in ANPR_V1]
    assert "number_plate" in names


def test_the_blank_preset_leaves_the_project_empty():
    project = _project("Seeded Blank")

    with SessionLocal() as db:
        class_definitions.seed_project_classes(db, project.id, "blank")
        assert class_definitions.list_project_classes(db, project.id) == []


def test_editing_one_project_cannot_reach_another():
    """The entire point of copying a preset rather than sharing it."""
    first = _project("Preset Isolation A")
    second = _project("Preset Isolation B")

    with SessionLocal() as db:
        class_definitions.seed_project_classes(db, first.id, "atcc-v1")
        class_definitions.seed_project_classes(db, second.id, "atcc-v1")

    with SessionLocal() as db:
        renamed = class_definitions.list_project_classes(db, first.id)[0]
        renamed.name = "Motorbike"
        db.commit()

    with SessionLocal() as db:
        untouched = class_definitions.list_project_classes(db, second.id)[0]
        assert untouched.name == ATCC_V1[0].name

    # ...and the preset itself is not mutated either.
    assert get_preset("atcc-v1")[0].name == ATCC_V1[0].name


def test_classes_come_back_in_display_order():
    project = _project("Ordered Classes")

    with SessionLocal() as db:
        class_definitions.seed_project_classes(db, project.id, "atcc-v1")

    with SessionLocal() as db:
        # Push the first class to the end.
        classes = class_definitions.list_project_classes(db, project.id)
        classes[0].display_order = 999
        db.commit()

    with SessionLocal() as db:
        reordered = class_definitions.list_project_classes(db, project.id)

    assert reordered[-1].name == ATCC_V1[0].name
    assert reordered[0].name == ATCC_V1[1].name


def test_an_unknown_preset_is_rejected():
    """A typo must not silently produce a project with no classes, which
    would look like a labelling bug much later."""
    project = _project("Bad Preset")

    with SessionLocal() as db:
        with pytest.raises(ValueError):
            class_definitions.seed_project_classes(db, project.id, "atcc-v99")


def test_seeding_twice_does_not_duplicate_the_list():
    """Re-running the seeding migration, or a retried create, must not
    double every class."""
    project = _project("Seeded Twice")

    with SessionLocal() as db:
        class_definitions.seed_project_classes(db, project.id, "atcc-v1")
        class_definitions.seed_project_classes(db, project.id, "atcc-v1")

    with SessionLocal() as db:
        assert len(class_definitions.list_project_classes(db, project.id)) == len(ATCC_V1)


def test_a_valid_class_id_is_one_of_the_projects_own():
    first = _project("Validation A")
    second = _project("Validation B")

    with SessionLocal() as db:
        class_definitions.seed_project_classes(db, first.id, "atcc-v1")
        class_definitions.seed_project_classes(db, second.id, "anpr-v1")

    with SessionLocal() as db:
        # 20 exists in ATCC but not in the two-class ANPR project.
        assert class_definitions.is_valid_class_id(db, first.id, 20) is True
        assert class_definitions.is_valid_class_id(db, second.id, 20) is False
        assert class_definitions.is_valid_class_id(db, second.id, 2) is True


def test_the_compatibility_shape_is_id_and_name():
    """Export, evaluation and the review UI all consume this shape."""
    project = _project("Schema Shape")

    with SessionLocal() as db:
        class_definitions.seed_project_classes(db, project.id, "anpr-v1")
        schema = class_definitions.class_schema_for(db, project.id)

    assert schema == [{"id": 1, "name": "vehicle"}, {"id": 2, "name": "number_plate"}]


def test_a_project_with_no_classes_reports_an_empty_schema():
    project = _project("No Classes At All")

    with SessionLocal() as db:
        assert class_definitions.class_schema_for(db, project.id) == []
        assert class_definitions.is_valid_class_id(db, project.id, 1) is False


def test_class_rows_belong_to_exactly_one_project():
    project = _project("Ownership")

    with SessionLocal() as db:
        class_definitions.seed_project_classes(db, project.id, "anpr-v1")

    with SessionLocal() as db:
        rows = db.query(ClassDefinition).filter(ClassDefinition.project_id == project.id).all()
        assert {r.project_id for r in rows} == {project.id}
