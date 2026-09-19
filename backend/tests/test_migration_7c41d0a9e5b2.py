"""The plate-text migration, run for real on a throwaway database.

It carries data and deletes rows, so it needs exercising rather than
trusting. Three things must hold: a human reading lands on the
annotation for its track, the row it came from goes, and a reading with
no annotation to attach to is left alone rather than thrown away.
"""

import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from tests.test_migration_2346228ab587 import _insert

BACKEND = Path(__file__).resolve().parents[1]
BEFORE = "2346228ab587"
THIS = "7c41d0a9e5b2"


@pytest.fixture
def migrated_db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'plate-migration.db').as_posix()}"
    previous = os.environ.get("ANPR_DATABASE_URL")
    os.environ["ANPR_DATABASE_URL"] = url
    get_settings.cache_clear()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    engine = create_engine(url)
    try:
        yield cfg, engine
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("ANPR_DATABASE_URL", None)
        else:
            os.environ["ANPR_DATABASE_URL"] = previous
        get_settings.cache_clear()


def _seed(connection, *, with_annotation: bool, attributes: dict | None = None) -> dict:
    """One project through to a human OCR row, optionally with a human
    annotation for the reading to land on.

    Built with the other migration test's schema-reading `_insert`, so
    the rows match the schema *at that revision* rather than today's
    models - which is the whole point of testing a migration.
    """
    project = _insert(connection, "projects", name="Plate Migration", workspace_path="/w", class_schema_version="anpr-v1")
    source = _insert(connection, "sources", project_id=project, type="video", path_or_uri="/v.mp4")
    frame = _insert(connection, "frames", source_id=source, frame_index=0, width=64, height=48)
    run = _insert(connection, "processing_runs", source_id=source, sampling_config="{}", status="completed")
    track = _insert(connection, "tracks", run_id=run, review_status="accepted")
    candidate = _insert(
        connection,
        "frame_candidates",
        track_id=track,
        frame_id=frame,
        image_path="/c.jpg",
        bbox_json="[1,1,11,11]",
        detector_class="car",
    )
    annotation = None
    if with_annotation:
        annotation = _insert(
            connection,
            "annotations",
            frame_id=frame,
            frame_candidate_id=candidate,
            source="human",
            class_id=4,
            bbox_json="[1,1,11,11]",
            attributes=json.dumps(attributes or {}),
            status="accepted",
        )
    ocr = _insert(
        connection,
        "ocr_candidates",
        track_id=track,
        frame_candidate_id=candidate,
        source="human",
        plate_bbox_json="[0,0,0,0]",
        text="mh 12 ab 1234",
        normalized_text="MH12AB1234",
        confidence=1.0,
        selected=1,
    )
    return {"track": track, "candidate": candidate, "annotation": annotation, "ocr": ocr}


def test_a_human_reading_moves_onto_the_annotation(migrated_db):
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        attributes = connection.execute(
            text("SELECT attributes FROM annotations WHERE id = :id"), {"id": ids["annotation"]}
        ).scalar()
        assert json.loads(attributes)["plate_text"] == "MH12AB1234"
        assert connection.execute(
            text("SELECT count(*) FROM ocr_candidates WHERE id = :id"), {"id": ids["ocr"]}
        ).scalar() == 0, "the row it came from goes, or there are two homes again"


def test_a_plate_already_on_the_annotation_is_not_overwritten(migrated_db):
    """A plate typed on the canvas is the newer of the two by
    construction, and a migration cannot see which the user meant."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True, attributes={"plate_text": "DL3C1234"})

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        attributes = connection.execute(
            text("SELECT attributes FROM annotations WHERE id = :id"), {"id": ids["annotation"]}
        ).scalar()
        assert json.loads(attributes)["plate_text"] == "DL3C1234"


def test_other_attributes_survive_the_move(migrated_db):
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True, attributes={"colour": "white", "occluded": True})

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        attributes = json.loads(
            connection.execute(
                text("SELECT attributes FROM annotations WHERE id = :id"), {"id": ids["annotation"]}
            ).scalar()
        )
        assert attributes == {"colour": "white", "occluded": True, "plate_text": "MH12AB1234"}


def test_a_reading_with_no_label_to_attach_to_is_left_alone(migrated_db):
    """Losing someone's typing to a migration would be worse than
    leaving an inert row behind. Nothing reads or writes it now."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=False)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM ocr_candidates WHERE id = :id"), {"id": ids["ocr"]}
        ).scalar() == 1


def test_model_readings_are_untouched(migrated_db):
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True)
        _insert(
            connection,
            "ocr_candidates",
            track_id=ids["track"],
            frame_candidate_id=ids["candidate"],
            source="model",
            plate_bbox_json="[0,0,10,5]",
            text="MH12AB1234",
            normalized_text="MH12AB1234",
            confidence=0.8,
            selected=0,
        )

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        rows = connection.execute(text("SELECT source FROM ocr_candidates")).scalars().all()
        assert rows == ["model"]


def test_an_empty_database_migrates_cleanly(migrated_db):
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM ocr_candidates")).scalar() == 0


def _human_row(connection, ids, *, text, normalized, selected=0):
    return _insert(
        connection,
        "ocr_candidates",
        track_id=ids["track"],
        frame_candidate_id=ids["candidate"],
        source="human",
        plate_bbox_json="[0,0,0,0]",
        text=text,
        normalized_text=normalized,
        confidence=1.0,
        selected=selected,
    )


def _plate(connection, annotation_id):
    raw = connection.execute(
        text("SELECT attributes FROM annotations WHERE id = :id"), {"id": annotation_id}
    ).scalar()
    return json.loads(raw or "{}").get("plate_text")


def _human_rows_left(connection) -> int:
    return connection.execute(text("SELECT count(*) FROM ocr_candidates WHERE source = 'human'")).scalar()


def test_the_reading_the_old_ui_showed_as_current_is_the_one_that_moves(migrated_db):
    """Correcting a plate twice left two human rows on one track. Taking
    whichever the database returned first moved the superseded one and
    deleted the current one - so the plate on screen was the one thrown
    away."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True)
        connection.execute(text("DELETE FROM ocr_candidates"))
        _human_row(connection, ids, text="mh 12 ab 1234", normalized="MH12AB1234", selected=0)
        _human_row(connection, ids, text="mh 12 ab 9999", normalized="MH12AB9999", selected=1)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) == "MH12AB9999", "the current reading, not the superseded one"


def test_a_conflicting_reading_is_kept_rather_than_deleted(migrated_db):
    """The annotation's plate wins - it is the newer of the two - but the
    reading it beat is a different value somebody typed, and deleting it
    is exactly the loss this migration promises not to cause."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True, attributes={"plate_text": "DL3C1234"})

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) == "DL3C1234"
        assert _human_rows_left(connection) == 1, "the reading it beat is still there to look at"


def test_a_reading_that_agrees_with_the_annotation_is_cleared_away(migrated_db):
    """Nothing is lost, so nothing is kept."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True, attributes={"plate_text": "MH12AB1234"})

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) == "MH12AB1234"
        assert _human_rows_left(connection) == 0


def test_a_reading_too_long_to_be_a_plate_is_not_written(migrated_db):
    """The old endpoint had no length limit and the attribute validator
    does. Writing one past it would leave a frame nothing could save -
    the same lockout the previous commit existed to remove, through a
    path that skips the validator."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True)
        connection.execute(text("DELETE FROM ocr_candidates"))
        _human_row(connection, ids, text="X" * 40, normalized="X" * 40, selected=1)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) is None
        assert _human_rows_left(connection) == 1, "left where it is rather than written somewhere it breaks things"


def test_a_reading_with_only_raw_text_is_canonicalised_the_way_the_app_would(migrated_db):
    """`str.isalnum` is Unicode-aware and the app's normaliser is not, so
    a hand-rolled one writes values the app could never produce and that
    never compare equal to anything."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True)
        connection.execute(text("DELETE FROM ocr_candidates"))
        _human_row(connection, ids, text="mhé-12", normalized="", selected=1)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) == "MH12"


def test_the_migrations_copies_of_the_apps_rules_have_not_drifted():
    """It inlines the normaliser and the length limit so it keeps
    working when the code moves on. Inlining is only safe if something
    notices when the original changes."""
    from app.services.annotation_attributes import PLATE_TEXT_MAX_LENGTH
    from app.services.text_normalization import normalize_plate_text

    module = _load_migration()

    assert module._PLATE_TEXT_MAX_LENGTH == PLATE_TEXT_MAX_LENGTH
    for raw in ("mh 12 ab 1234", "MHé-12", "१२३", "", "dl3c 9999"):
        assert module._normalize(raw) == normalize_plate_text(raw), raw


def _load_migration():
    import importlib.util

    path = BACKEND / "alembic" / "versions" / "7c41d0a9e5b2_move_human_plate_text_onto_annotations.py"
    spec = importlib.util.spec_from_file_location("_plate_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_current_reading_that_says_nothing_does_not_take_the_others_with_it(migrated_db):
    """A correction of "!!!" normalises to nothing. It is the current
    reading, so nothing is written - and the earlier reading it replaced
    must still be there, not deleted along with it."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True)
        connection.execute(text("DELETE FROM ocr_candidates"))
        _human_row(connection, ids, text="dl 3c 1234", normalized="DL3C1234", selected=0)
        _human_row(connection, ids, text="!!!", normalized="", selected=1)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) is None
        kept = connection.execute(
            text("SELECT normalized_text FROM ocr_candidates WHERE source = 'human'")
        ).scalars().all()
        assert kept == ["DL3C1234"], "the reading that says nothing goes; the one that says something stays"


def test_a_superseded_reading_with_different_text_is_kept(migrated_db):
    """Only the current reading lands on the annotation, so an earlier
    one saying something else is a value with nowhere to go - and the
    rule this migration states is that those are left alone."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True)
        connection.execute(text("DELETE FROM ocr_candidates"))
        _human_row(connection, ids, text="mh 12 ab 1234", normalized="MH12AB1234", selected=0)
        _human_row(connection, ids, text="mh 12 ab 9999", normalized="MH12AB9999", selected=1)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) == "MH12AB9999"
        kept = connection.execute(
            text("SELECT normalized_text FROM ocr_candidates WHERE source = 'human'")
        ).scalars().all()
        assert kept == ["MH12AB1234"], "its text is nowhere else, so it stays"


def test_a_superseded_reading_that_agrees_is_cleared_away(migrated_db):
    """Saying the same thing twice is not a second value."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True)
        connection.execute(text("DELETE FROM ocr_candidates"))
        _human_row(connection, ids, text="mh 12 ab 1234", normalized="MH12AB1234", selected=0)
        _human_row(connection, ids, text="MH12AB1234", normalized="MH12AB1234", selected=1)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) == "MH12AB1234"
        assert _human_rows_left(connection) == 0


def test_a_reading_the_annotation_disagrees_with_is_kept_even_when_superseded(migrated_db):
    """The conflict rule applies to every row, not just the current one."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True, attributes={"plate_text": "KA01AA1111"})
        connection.execute(text("DELETE FROM ocr_candidates"))
        _human_row(connection, ids, text="mh 12 ab 1234", normalized="MH12AB1234", selected=0)
        _human_row(connection, ids, text="KA01AA1111", normalized="KA01AA1111", selected=1)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) == "KA01AA1111"
        kept = connection.execute(
            text("SELECT normalized_text FROM ocr_candidates WHERE source = 'human'")
        ).scalars().all()
        assert kept == ["MH12AB1234"], "the one that agrees goes, the one that does not stays"


def test_a_stored_reading_is_canonicalised_rather_than_copied(migrated_db):
    """`normalized_text` is a column somebody could have hand-edited. The
    migration writes what the app would store, not what the row claims."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as connection:
        ids = _seed(connection, with_annotation=True)
        connection.execute(text("DELETE FROM ocr_candidates"))
        _human_row(connection, ids, text="mh 12 ab 1234", normalized="mh 12 ab 1234", selected=1)

    command.upgrade(cfg, THIS)

    with engine.connect() as connection:
        assert _plate(connection, ids["annotation"]) == "MH12AB1234"
