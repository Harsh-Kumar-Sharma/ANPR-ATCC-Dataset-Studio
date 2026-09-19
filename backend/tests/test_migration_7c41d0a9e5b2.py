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
