"""Bringing your own model into the app.

The registry already listed whatever was in the models directory, but
getting a file in there meant copying it by hand and knowing that the
app would look. This is the import: checked when it arrives, named,
and removable again.
"""

import json

import pytest

from app.core.errors import NotFoundError
from app.ml import models as registry
from app.ml.models import sidecar_path
from app.services import model_import
from app.services.model_import import ModelImportError, import_model, remove_model, safe_id


@pytest.fixture
def loads_fine(monkeypatch):
    """Every checkpoint loads, and reports these classes.

    Loading a real one means torch and a real file; what is being
    tested here is everything around that, so the load is replaced
    and tested on its own below.
    """
    monkeypatch.setattr(model_import, "_read_classes", lambda path: ["plate", "vehicle"])


def _a_checkpoint(path, content=b"weights that are not really weights"):
    path.write_bytes(content)
    return path


# --- taking one in ------------------------------------------------------------


def test_a_model_you_trained_becomes_one_you_can_choose(tmp_path, loads_fine):
    models = tmp_path / "models"
    source = _a_checkpoint(tmp_path / "gantry_v3.pt")

    imported = import_model(source, models)

    assert imported.model.id == "gantry_v3"
    assert imported.model.kind == "custom"
    assert imported.model.id in [m.id for m in registry.list_models(models)]


def test_the_file_is_copied_rather_than_pointed_at(tmp_path, loads_fine):
    """A path into someone's Downloads folder is a model that
    disappears, and a run that fails later with a missing file is a
    worse answer than a copy."""
    models = tmp_path / "models"
    source = _a_checkpoint(tmp_path / "gantry_v3.pt")

    import_model(source, models)
    source.unlink()

    assert (models / "gantry_v3.pt").is_file()
    assert registry.get_model("gantry_v3", models).present is True


def test_it_can_be_given_a_name_of_its_own(tmp_path, loads_fine):
    models = tmp_path / "models"
    source = _a_checkpoint(tmp_path / "best.pt")

    imported = import_model(source, models, name="Gantry night v3")

    assert imported.model.id == "Gantry-night-v3"
    assert imported.model.label == "Gantry night v3"


@pytest.mark.parametrize(
    "name,expected",
    [("../escape", "escape"), ("a/b", "a-b"), ("  spaced  out ", "spaced-out"), ("", "model")],
)
def test_a_name_is_made_safe_before_it_becomes_a_path(name, expected):
    assert safe_id(name) == expected


def test_what_the_model_detects_is_recorded_beside_it(tmp_path, loads_fine):
    models = tmp_path / "models"
    import_model(_a_checkpoint(tmp_path / "gantry_v3.pt"), models)

    stored = json.loads(sidecar_path(models, "gantry_v3").read_text(encoding="utf-8"))

    assert stored["classes"] == ["plate", "vehicle"]
    assert registry.get_model("gantry_v3", models).classes == ["plate", "vehicle"]


def test_the_menu_says_what_a_custom_model_detects(tmp_path, loads_fine):
    """Choosing between "yolo26n" and "gantry_v3" is not a choice
    unless you are told what the second one does."""
    models = tmp_path / "models"
    import_model(_a_checkpoint(tmp_path / "gantry_v3.pt"), models)

    note = registry.get_model("gantry_v3", models).note

    assert "plate" in note and "vehicle" in note


# --- refusing what is not a model ---------------------------------------------


def test_a_file_that_is_not_a_checkpoint_is_refused(tmp_path):
    models = tmp_path / "models"
    source = tmp_path / "notes.txt"
    source.write_text("not a model", encoding="utf-8")

    with pytest.raises(ModelImportError, match=r"\.pt"):
        import_model(source, models)


def test_a_file_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(NotFoundError):
        import_model(tmp_path / "missing.pt", tmp_path / "models")


def test_an_empty_file_is_refused(tmp_path):
    models = tmp_path / "models"
    source = _a_checkpoint(tmp_path / "empty.pt", b"")

    with pytest.raises(ModelImportError, match="empty"):
        import_model(source, models)


def test_a_checkpoint_that_does_not_load_is_refused_and_leaves_nothing_behind(tmp_path, monkeypatch):
    """The whole reason import loads the file: the alternative is a
    run that fails two minutes in with a confusing error."""
    models = tmp_path / "models"

    def will_not_load(path):
        raise ModelImportError("that file did not load as a YOLO model")

    monkeypatch.setattr(model_import, "_read_classes", will_not_load)

    with pytest.raises(ModelImportError):
        import_model(_a_checkpoint(tmp_path / "broken.pt"), models)

    assert list(models.glob("*")) == [], "a refused import must not leave a file to be offered later"


def test_taking_the_name_of_a_builtin_is_refused(tmp_path, loads_fine):
    models = tmp_path / "models"

    with pytest.raises(ModelImportError, match="built-in"):
        import_model(_a_checkpoint(tmp_path / "yolo26n.pt"), models)


def test_importing_the_same_name_twice_is_refused_rather_than_overwriting(tmp_path, loads_fine):
    models = tmp_path / "models"
    import_model(_a_checkpoint(tmp_path / "gantry_v3.pt"), models)

    with pytest.raises(ModelImportError, match="already"):
        import_model(_a_checkpoint(tmp_path / "gantry_v3.pt"), models)


def test_a_half_copied_file_is_not_offered_as_a_model(tmp_path):
    """A copy in progress under the real name would be listed, and
    loaded, as a model."""
    models = tmp_path / "models"
    models.mkdir()
    (models / ".importing-gantry_v3.pt").write_bytes(b"half")

    assert [m.id for m in registry.list_models(models) if m.kind == "custom"] == []


# --- getting rid of one -------------------------------------------------------


def test_a_model_you_imported_can_be_removed(tmp_path, loads_fine):
    models = tmp_path / "models"
    import_model(_a_checkpoint(tmp_path / "gantry_v3.pt"), models)

    remove_model("gantry_v3", models)

    assert [m.id for m in registry.list_models(models) if m.kind == "custom"] == []
    assert not sidecar_path(models, "gantry_v3").exists()


def test_a_builtin_cannot_be_removed(tmp_path):
    """The id is not yours to free, and the next run would download
    the weights again anyway."""
    models = tmp_path / "models"
    models.mkdir()
    (models / "yolo26n.pt").write_bytes(b"weights")

    with pytest.raises(ModelImportError, match="built-in"):
        remove_model("yolo26n", models)

    assert (models / "yolo26n.pt").is_file()


# --- a model dropped in by hand still works -----------------------------------


def test_a_file_copied_in_by_hand_is_still_offered(tmp_path):
    """The models directory is the truth about which models exist.
    Importing is the convenient way in, not the only one."""
    models = tmp_path / "models"
    models.mkdir()
    (models / "dropped_in.pt").write_bytes(b"weights")

    model = registry.get_model("dropped_in", models)

    assert model.kind == "custom"
    assert model.classes == []


# --- a custom model's classes are its own -------------------------------------


def test_a_custom_model_is_not_filtered_against_coco_vehicle_names(tmp_path, monkeypatch, loads_fine):
    """The trap this avoids: the detector filters detections against a
    list of COCO vehicle names. A plate detector's "plate" is not on
    it, so a model you trained would have found things all day and
    reported nothing at all.
    """
    from app.core.config import get_settings
    from app.ml import factory

    models = tmp_path / "models"
    import_model(_a_checkpoint(tmp_path / "gantry_v3.pt"), models)

    built: dict = {}

    class _Recording:
        def __init__(self, weights, name=None, device=None, class_allowlist=None):
            built[name] = class_allowlist
            self.model_version = name

    monkeypatch.setattr(factory, "YoloDetector", _Recording)
    monkeypatch.setattr(factory, "ensure_weights", lambda model_id, directory: directory / f"{model_id}.pt")
    monkeypatch.setattr(get_settings(), "model_weights_dir", models, raising=False)
    factory.get_detector.cache_clear()
    try:
        factory.get_detector("gantry_v3")
        factory.get_detector("yolo26n")
    finally:
        factory.get_detector.cache_clear()

    assert built["gantry_v3"] is None, "a model you trained keeps everything it finds"
    assert built["yolo26n"] is not None, "a COCO model still keeps people out of a vehicle dataset"


# --- through the API ----------------------------------------------------------


def test_the_api_imports_a_model_and_then_offers_it(tmp_path, monkeypatch, loads_fine):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    models = tmp_path / "models"
    monkeypatch.setattr(get_settings(), "model_weights_dir", models, raising=False)
    client = TestClient(app)
    source = _a_checkpoint(tmp_path / "gantry_v3.pt")

    created = client.post("/models", json={"path": str(source)})

    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "custom"
    assert "gantry_v3" in [m["id"] for m in client.get("/models").json()]


def test_the_api_says_what_is_wrong_with_a_file_it_will_not_take(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    monkeypatch.setattr(get_settings(), "model_weights_dir", tmp_path / "models", raising=False)
    client = TestClient(app)
    notes = tmp_path / "notes.txt"
    notes.write_text("not a model", encoding="utf-8")

    refused = client.post("/models", json={"path": str(notes)})

    assert refused.status_code == 400
    assert ".pt" in refused.json()["message"]


def test_the_api_removes_a_model_you_imported(tmp_path, monkeypatch, loads_fine):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    models = tmp_path / "models"
    monkeypatch.setattr(get_settings(), "model_weights_dir", models, raising=False)
    client = TestClient(app)
    client.post("/models", json={"path": str(_a_checkpoint(tmp_path / "gantry_v3.pt"))})

    removed = client.delete("/models/gantry_v3")

    assert removed.status_code == 204
    assert "gantry_v3" not in [m["id"] for m in client.get("/models").json()]
