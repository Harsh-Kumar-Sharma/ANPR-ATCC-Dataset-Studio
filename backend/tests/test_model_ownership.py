"""Which project a model belongs to, for models that predate the idea.

Models were a flat directory shared by every project, so eleven of
them turned up in all three menus with nothing saying which was
which. Training runs know: each records the project it ran for and
the model it produced.
"""

from fastapi.testclient import TestClient

from app.db.models.training_run import TrainingRun
from app.db.session import SessionLocal
from app.ml import models as registry
from app.main import app
from app.services import model_ownership

client = TestClient(app)


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _checkpoint(models, model_id: str) -> None:
    models.mkdir(parents=True, exist_ok=True)
    (models / f"{model_id}.pt").write_bytes(b"weights")


def _training_run(project_id: str, model_id: str) -> None:
    """A finished run that produced ``model_id``.

    The dataset version is a name rather than a real row: nothing
    here reads it, and SQLite does not enforce the foreign key.
    """
    with SessionLocal() as db:
        db.add(
            TrainingRun(
                project_id=project_id,
                dataset_version_id="dv-unused",
                base_model_id="yolo26n",
                epochs=1,
                image_size=640,
                status="completed",
                output_model_id=model_id,
            )
        )
        db.commit()


def test_a_model_is_given_the_project_its_training_run_names(tmp_path):
    project = _project("Owns A Model")
    models = tmp_path / "models"
    _checkpoint(models, "yolo26n-v1")
    _training_run(project["id"], "yolo26n-v1")

    with SessionLocal() as db:
        claimed = model_ownership.backfill(db, models)

    assert "yolo26n-v1" in claimed
    assert registry.get_model("yolo26n-v1", models).project_id == project["id"]


def test_an_owner_already_recorded_is_not_second_guessed(tmp_path):
    project = _project("Already Owned")
    models = tmp_path / "models"
    _checkpoint(models, "already-owned")
    registry.write_sidecar(models, "already-owned", project_id="p-somewhere-else")
    _training_run(project["id"], "already-owned")

    with SessionLocal() as db:
        claimed = model_ownership.backfill(db, models)

    assert "already-owned" not in claimed
    assert registry.get_model("already-owned", models).project_id == "p-somewhere-else"


def test_a_model_whose_run_was_cleared_stays_ownerless(tmp_path):
    """Nothing knows any more, and ownerless means "shown
    everywhere" - the safe way to be ignorant."""
    models = tmp_path / "models"
    _checkpoint(models, "orphan")

    with SessionLocal() as db:
        claimed = model_ownership.backfill(db, models)

    assert "orphan" not in claimed
    assert registry.get_model("orphan", models).project_id is None
    assert "orphan" in [m.id for m in registry.list_models(models, project_id="p-anything")]


def test_backfilling_twice_changes_nothing_the_second_time(tmp_path):
    project = _project("Idempotent Backfill")
    models = tmp_path / "models"
    _checkpoint(models, "twice")
    _training_run(project["id"], "twice")

    with SessionLocal() as db:
        first = model_ownership.backfill(db, models)
        second = model_ownership.backfill(db, models)

    assert first == ["twice"]
    assert second == []


def test_builtins_are_never_given_an_owner(tmp_path):
    """They detect COCO classes, which belong to nobody."""
    models = tmp_path / "models"
    _checkpoint(models, "yolo26n")

    with SessionLocal() as db:
        claimed = model_ownership.backfill(db, models)

    assert claimed == []
    assert registry.get_model("yolo26n", models).project_id is None
