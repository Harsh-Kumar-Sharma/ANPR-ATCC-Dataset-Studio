"""The annotations-onto-frames migration, run for real on a throwaway
database.

The rest of the suite builds its schema with ``create_all`` and never
runs alembic, so a migration that carries data - and this one deletes
rows - had nothing exercising it. Two things must hold: a label whose
candidate has a frame keeps it, and a label whose candidate does not is
dropped *with* everything that depended on it.
"""

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import get_settings

BACKEND = Path(__file__).resolve().parents[1]
BEFORE = "e76306d9efc0"
THIS = "2346228ab587"


@pytest.fixture
def migrated_db(tmp_path):
    """An alembic config pointed at a fresh database, restored afterwards.

    env.py reads the URL from settings, which are cached, so the cache
    is cleared around the swap in both directions.
    """
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
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


def _insert(conn, table: str, **values) -> str:
    """Insert a row, filling every NOT NULL column this test does not care
    about with a type-appropriate placeholder. Keeps the seed tied to the
    schema *at that revision* rather than to today's models."""
    columns = conn.execute(text(f"PRAGMA table_info({table})")).all()
    row = {}
    for _cid, name, ctype, notnull, default, _pk in columns:
        if name in values:
            row[name] = values[name]
            continue
        if not notnull or default is not None:
            continue
        kind = ctype.upper()
        if name == "id":
            row[name] = str(uuid.uuid4())
        elif "INT" in kind:
            row[name] = 0
        elif "FLOAT" in kind or "REAL" in kind or "NUM" in kind:
            row[name] = 0.0
        elif "DATE" in kind or "TIME" in kind:
            row[name] = "2026-01-01 00:00:00"
        elif "JSON" in kind:
            row[name] = "{}"
        elif "BOOL" in kind:
            row[name] = 0
        else:
            row[name] = "x"
    if "id" not in row:
        row["id"] = str(uuid.uuid4())
    placeholders = ", ".join(f":{k}" for k in row)
    conn.execute(text(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({placeholders})"), row)
    return row["id"]


def _seed_pre_migration_project(conn, *, candidate_has_frame: bool) -> dict:
    project = _insert(conn, "projects", name="M", workspace_path="/w", class_schema_version="atcc-v1")
    source = _insert(conn, "sources", project_id=project, type="video", path_or_uri="/v.mp4")
    run = _insert(conn, "processing_runs", source_id=source, sampling_config="{}", status="completed")
    track = _insert(conn, "tracks", run_id=run, review_status="accepted")
    frame = _insert(conn, "frames", source_id=source, frame_index=1, width=64, height=48) if candidate_has_frame else None
    candidate = _insert(
        conn, "frame_candidates", track_id=track, frame_id=frame, image_path="/c.jpg", bbox_json="[0,0,1,1]", detector_class="car"
    )
    annotation = _insert(
        conn, "annotations", frame_candidate_id=candidate, source="human", class_id=4, bbox_json="[0,0,1,1]", status="accepted"
    )
    version = _insert(conn, "dataset_versions", project_id=project, version=1, config_snapshot_json="{}")
    item = _insert(conn, "dataset_items", dataset_version_id=version, annotation_id=annotation, split="train", export_path="i.jpg")
    return {"track": track, "frame": frame, "annotation": annotation, "item": item}


def test_a_label_whose_candidate_has_a_frame_keeps_it(migrated_db):
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as conn:
        seeded = _seed_pre_migration_project(conn, candidate_has_frame=True)

    command.upgrade(cfg, THIS)

    with engine.begin() as conn:
        frame_id = conn.execute(text("SELECT frame_id FROM annotations WHERE id = :id"), {"id": seeded["annotation"]}).scalar()
        assert frame_id == seeded["frame"]
        assert conn.execute(text("SELECT COUNT(*) FROM dataset_items")).scalar() == 1
        assert conn.execute(text("SELECT review_status FROM tracks")).scalar() == "accepted"


def test_a_label_with_no_frame_is_dropped_together_with_what_depended_on_it(migrated_db):
    """Nothing enforces the foreign keys, so the migration has to settle
    the dataset item and the track itself - the same rule the app's own
    annotation deletion follows."""
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as conn:
        _seed_pre_migration_project(conn, candidate_has_frame=False)

    command.upgrade(cfg, THIS)

    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM annotations")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM dataset_items")).scalar() == 0
        assert conn.execute(text("SELECT review_status FROM tracks")).scalar() == "unreviewed"


def test_downgrade_drops_canvas_boxes_and_their_dataset_items_but_keeps_legacy_labels(migrated_db):
    cfg, engine = migrated_db
    command.upgrade(cfg, BEFORE)
    with engine.begin() as conn:
        seeded = _seed_pre_migration_project(conn, candidate_has_frame=True)
    command.upgrade(cfg, THIS)

    with engine.begin() as conn:
        canvas = _insert(
            conn, "annotations", frame_id=seeded["frame"], frame_candidate_id=None, source="human", bbox_json="[0,0,1,1]", status="accepted"
        )
        version = conn.execute(text("SELECT id FROM dataset_versions")).scalar()
        _insert(conn, "dataset_items", dataset_version_id=version, annotation_id=canvas, split="train", export_path="c.jpg")

    command.downgrade(cfg, BEFORE)

    with engine.begin() as conn:
        ids = {row[0] for row in conn.execute(text("SELECT id FROM annotations"))}
        assert ids == {seeded["annotation"]}
        item_targets = {row[0] for row in conn.execute(text("SELECT annotation_id FROM dataset_items"))}
        assert item_targets == {seeded["annotation"]}
