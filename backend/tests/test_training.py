"""Training a model inside the app.

The app used to write a data.yaml and a RETRAINING.md and stop there,
leaving the user to copy a command into a terminal. This is that
command, run on the job machinery that already carries long work.

The training itself is replaced throughout. What is being tested is
everything around it: what is refused, what is recorded, what happens
when it is cancelled, and whether the result can actually be used.
"""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.errors import NotFoundError
from app.db.models.training_run import TrainingRun
from app.db.session import SessionLocal
from app.main import app
from app.ml.types import Detection
from app.services import training
from app.services.jobs import handlers
from app.services.training import TrainingBusyError, TrainingProgress, TrainingUnavailableError
from tests.job_execution import run_jobs_inline
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96
CAR_CLASS = 4


class _OneCarDetector:
    model_version = "one-car-stub-v1"
    class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _exported_version(tmp_path, name: str) -> tuple[dict, dict]:
    """A project with one labelled frame and an exported version."""
    from tests.job_execution import process_source_sync

    project = _project(name)
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=6, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)

    for frame in client.get(f"/projects/{project['id']}/frames").json()[:2]:
        client.put(
            f"/frames/{frame['id']}/annotations",
            json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
        )

    exported = client.post(f"/projects/{project['id']}/dataset-versions", json={})
    assert exported.status_code in (200, 201), exported.text
    return project, exported.json()["dataset_version"]


@pytest.fixture(autouse=True)
def gpu_is_free():
    """No training run left over from another test.

    "One at a time" is deliberately global - this machine has one GPU,
    not one per project - so a run a previous test left pending would
    refuse every run after it.
    """
    with SessionLocal() as db:
        # Every run, not only the unfinished ones. "One at a time" is
        # deliberately global - this machine has one GPU, not one per
        # project - so training runs are the one thing in this suite
        # where tests genuinely interfere with each other, and the
        # only clean answer is to start each with none.
        #
        # Listed before deleting: deleting while the query is still
        # streaming flushes mid-iteration and quietly skips rows.
        for run in db.query(TrainingRun).all():
            db.delete(run)
        db.commit()
    yield


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    """A models directory of this test's own, with a base checkpoint."""
    from app.core.config import get_settings

    directory = tmp_path / "models"
    directory.mkdir()
    (directory / "yolo26n.pt").write_bytes(b"pretend base weights")
    monkeypatch.setattr(get_settings(), "model_weights_dir", directory, raising=False)
    return directory


@pytest.fixture
def fake_training(monkeypatch):
    """Training that writes a checkpoint and reports two epochs."""
    calls: list[dict] = []

    def train(weights, data_yaml, output_dir, epochs, image_size, device, on_epoch, augmentation=None):
        calls.append(
            {
                "weights": Path(weights),
                "data_yaml": Path(data_yaml),
                "epochs": epochs,
                "image_size": image_size,
                "augmentation": augmentation,
            }
        )
        on_epoch(TrainingProgress(epoch=1, epochs=epochs, loss=1.5, map50=0.20))
        on_epoch(TrainingProgress(epoch=2, epochs=epochs, loss=0.9, map50=0.55))
        best = Path(output_dir) / "run" / "weights" / "best.pt"
        best.parent.mkdir(parents=True, exist_ok=True)
        best.write_bytes(b"trained weights")
        return best

    monkeypatch.setattr(handlers, "train", train)
    # Testing the trained model, without loading one.
    monkeypatch.setattr(
        handlers,
        "evaluate",
        lambda weights, data_yaml, image_size, device: {"precision": 0.9, "recall": 0.95, "map50": 0.93, "map50_95": 0.6},
    )
    # Nothing goes near the network for a base model's weights either.
    monkeypatch.setattr(training, "ensure_weights", lambda model_id, directory: directory / f"{model_id}.pt")
    return calls


def _start(project: dict, version: dict, **over) -> "tuple[int, dict]":
    body = {"dataset_version_id": version["id"], "base_model_id": "yolo26n", "epochs": 2, **over}
    response = client.post(f"/projects/{project['id']}/training-runs", json=body)
    return response.status_code, (response.json() if response.content else {})


# --- what a run records -------------------------------------------------------


def test_training_runs_and_records_what_it_was_made_from(tmp_path, models_dir, fake_training):
    """A model in the directory is a file, and six weeks later nobody
    remembers which labels went into it."""
    project, version = _exported_version(tmp_path, "Training Records")

    with run_jobs_inline():
        status, body = _start(project, version)

    assert status == 202, body
    run = client.get(f"/training-runs/{body['run']['id']}").json()
    assert run["status"] == "completed"
    assert run["dataset_version_id"] == version["id"]
    assert run["base_model_id"] == "yolo26n"
    assert run["epochs"] == 2


def test_the_trained_model_can_be_detected_with(tmp_path, models_dir, fake_training):
    """The whole loop: label, train, detect with what you trained."""
    project, version = _exported_version(tmp_path, "Training Produces A Model")

    with run_jobs_inline():
        _, body = _start(project, version)

    model_id = client.get(f"/training-runs/{body['run']['id']}").json()["output_model_id"]
    assert model_id
    assert model_id in [m["id"] for m in client.get("/models").json()]
    assert (models_dir / f"{model_id}.pt").read_bytes() == b"trained weights"


def test_the_model_is_named_after_what_made_it(tmp_path, models_dir, fake_training):
    """"best.pt" tells you nothing six weeks later, and the models
    directory is a flat list."""
    project, version = _exported_version(tmp_path, "Training Names The Model")

    with run_jobs_inline():
        _, body = _start(project, version)

    model_id = client.get(f"/training-runs/{body['run']['id']}").json()["output_model_id"]
    assert "yolo26n" in model_id
    assert f"v{version['version']}" in model_id


def test_training_the_same_pair_twice_does_not_overwrite_the_first(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Training Twice")

    with run_jobs_inline():
        _, first = _start(project, version)
    with run_jobs_inline():
        _, second = _start(project, version)

    one = client.get(f"/training-runs/{first['run']['id']}").json()["output_model_id"]
    two = client.get(f"/training-runs/{second['run']['id']}").json()["output_model_id"]
    assert one != two
    assert (models_dir / f"{one}.pt").is_file()


def test_progress_reports_the_epoch_and_the_metric(tmp_path, models_dir, fake_training):
    """"Training…" for an hour says nothing about whether it is
    working."""
    project, version = _exported_version(tmp_path, "Training Progress")

    with run_jobs_inline():
        _, body = _start(project, version)

    run = client.get(f"/training-runs/{body['run']['id']}").json()
    assert run["last_epoch"] == 2
    assert run["best_map50"] == pytest.approx(0.55)


def test_the_progress_message_says_more_than_a_spinner():
    progress = TrainingProgress(epoch=3, epochs=100, loss=1.234, map50=0.41)

    assert "Epoch 3 of 100" in progress.message()
    assert "1.234" in progress.message()
    assert "0.410" in progress.message()


def test_progress_never_claims_to_be_finished_while_it_is_not():
    """The run is not done until the weights are imported, and a bar
    at 100% with work still happening is a bar that lies."""
    assert TrainingProgress(epoch=100, epochs=100).fraction() < 1.0


# --- one at a time ------------------------------------------------------------


def test_a_second_training_run_is_refused_while_one_is_going(tmp_path, models_dir, fake_training):
    """This machine has one GPU. A second run would make both slower
    and could exhaust its memory outright."""
    project, version = _exported_version(tmp_path, "One At A Time")
    # Submitted but never executed, so it stays "pending".
    status, first = _start(project, version)
    assert status == 202, first

    refused_status, refused = _start(project, version)

    assert refused_status == 409
    assert refused["code"] == "training_busy"


def test_the_refusal_names_what_is_already_running(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Refusal Is Useful")
    _start(project, version)

    _, refused = _start(project, version)

    assert version["id"] in refused["message"]
    assert "cancel" in refused["message"].lower()


def test_another_run_can_start_once_the_first_has_finished(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Free Again")
    with run_jobs_inline():
        _start(project, version)

    status, body = _start(project, version)

    assert status == 202, body


# --- what is refused ----------------------------------------------------------


def test_training_on_a_version_that_was_never_exported_is_refused(tmp_path, models_dir, fake_training):
    """Finding this out an hour later through a failed job is a poor
    way to learn it."""
    project, version = _exported_version(tmp_path, "Not Exported")
    shutil.rmtree(Path(project["workspace_path"]) / "exports")

    status, body = _start(project, version)

    assert status == 400
    assert "export" in body["message"].lower()


def test_training_from_a_model_that_does_not_exist_is_refused(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Unknown Base Model")

    status, body = _start(project, version, base_model_id="no-such-model")

    assert status == 404


def test_a_version_from_another_project_is_refused(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Mine To Train")
    other = _project("Someone Else")

    response = client.post(
        f"/projects/{other['id']}/training-runs",
        json={"dataset_version_id": version["id"], "base_model_id": "yolo26n"},
    )

    assert response.status_code == 404


def test_training_is_refused_when_the_disk_is_nearly_full(tmp_path, models_dir, fake_training, monkeypatch):
    """A run that fills the disk at epoch 80 wastes an hour rather
    than a minute."""
    project, version = _exported_version(tmp_path, "No Room To Train")
    monkeypatch.setattr(training, "free_bytes_for", lambda path: 1024)

    status, body = _start(project, version)

    assert status == 400
    assert "free" in body["message"].lower()


# --- when it goes wrong -------------------------------------------------------


def test_a_failed_run_says_so_and_frees_the_gpu(tmp_path, models_dir, fake_training, monkeypatch):
    """A row that claims to be training holds the GPU against every
    future run."""
    def explode(**kwargs):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(handlers, "train", explode)
    project, version = _exported_version(tmp_path, "Training Fails")

    with run_jobs_inline():
        _, body = _start(project, version)

    run = client.get(f"/training-runs/{body['run']['id']}").json()
    assert run["status"] == "failed"
    assert "CUDA out of memory" in run["error_message"]
    assert run["output_model_id"] is None
    # And the next one can start.
    assert _start(project, version)[0] == 202


def test_a_cancelled_run_is_settled_and_keeps_its_checkpoints(tmp_path, models_dir, fake_training):
    """A cancelled run at epoch 60 has produced something, and
    deleting it would throw away an hour of GPU time."""
    project, version = _exported_version(tmp_path, "Training Cancelled")
    _, body = _start(project, version)
    run_id = body["run"]["id"]

    handlers.settle_train_job(SessionLocal(), {"training_run_id": run_id}, "cancelled")

    run = client.get(f"/training-runs/{run_id}").json()
    assert run["status"] == "cancelled"
    assert "checkpoints" in run["error_message"].lower()


def test_settling_does_not_rewrite_a_run_that_already_finished(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Settle Is Idempotent")
    with run_jobs_inline():
        _, body = _start(project, version)
    finished = client.get(f"/training-runs/{body['run']['id']}").json()

    with SessionLocal() as db:
        training.settle(db, body["run"]["id"], "cancelled", "should not apply")

    assert client.get(f"/training-runs/{body['run']['id']}").json() == finished


def test_an_unknown_run_is_a_404():
    assert client.get("/training-runs/not-a-run").status_code == 404


# --- continuing from your own model -------------------------------------------


def test_a_run_can_start_from_a_model_this_app_trained(tmp_path, models_dir, fake_training):
    """The loop the whole app exists to serve: label, train, detect
    with what you trained, label what it got wrong, train again."""
    project, version = _exported_version(tmp_path, "Continue From Mine")
    with run_jobs_inline():
        _, first = _start(project, version)
    mine = client.get(f"/training-runs/{first['run']['id']}").json()["output_model_id"]

    with run_jobs_inline():
        status, second = _start(project, version, base_model_id=mine)

    assert status == 202, second
    run = client.get(f"/training-runs/{second['run']['id']}").json()
    assert run["base_model_id"] == mine
    assert run["status"] == "completed"


def test_the_chain_back_to_the_base_model_is_visible(tmp_path, models_dir, fake_training):
    """Each run names the model it started from, so following the
    chain back is just reading the rows."""
    project, version = _exported_version(tmp_path, "Lineage")
    with run_jobs_inline():
        _, first = _start(project, version)
    first_model = client.get(f"/training-runs/{first['run']['id']}").json()["output_model_id"]
    with run_jobs_inline():
        _, second = _start(project, version, base_model_id=first_model)

    runs = client.get(f"/projects/{project['id']}/training-runs").json()

    by_id = {r["id"]: r for r in runs}
    child = by_id[second["run"]["id"]]
    parent = by_id[first["run"]["id"]]
    assert child["base_model_id"] == parent["output_model_id"]
    assert parent["base_model_id"] == "yolo26n"


def test_runs_are_listed_most_recent_first(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Recent First")
    with run_jobs_inline():
        _, first = _start(project, version)
    with run_jobs_inline():
        _, second = _start(project, version)

    runs = client.get(f"/projects/{project['id']}/training-runs").json()

    assert [r["id"] for r in runs][:2] == [second["run"]["id"], first["run"]["id"]]


# --- the device the run is given ----------------------------------------------


def test_training_is_given_a_device_ultralytics_understands(tmp_path, models_dir, monkeypatch):
    """"auto" is this app's own word for "use the GPU if there is
    one". Ultralytics has never heard of it and refuses the run:
    "Invalid CUDA 'device=auto' requested".
    """
    from app.core.config import get_settings

    seen: dict = {}

    def train(weights, data_yaml, output_dir, epochs, image_size, device, on_epoch, augmentation=None):
        seen["device"] = device
        best = Path(output_dir) / "run" / "weights" / "best.pt"
        best.parent.mkdir(parents=True, exist_ok=True)
        best.write_bytes(b"trained weights")
        return best

    monkeypatch.setattr(handlers, "train", train)
    monkeypatch.setattr(training, "ensure_weights", lambda model_id, directory: directory / f"{model_id}.pt")
    monkeypatch.setattr(get_settings(), "device", "auto", raising=False)
    project, version = _exported_version(tmp_path, "Device Is Resolved")

    with run_jobs_inline():
        _start(project, version)

    assert seen["device"] != "auto"
    assert seen["device"] in ("cpu",) or seen["device"].startswith("cuda:")


def test_an_explicit_cpu_setting_is_honoured(tmp_path, models_dir, monkeypatch):
    """"cpu" is an override, not a hint - someone who set it wants
    the GPU left alone."""
    from app.core.config import get_settings

    seen: dict = {}

    def train(weights, data_yaml, output_dir, epochs, image_size, device, on_epoch, augmentation=None):
        seen["device"] = device
        best = Path(output_dir) / "run" / "weights" / "best.pt"
        best.parent.mkdir(parents=True, exist_ok=True)
        best.write_bytes(b"trained weights")
        return best

    monkeypatch.setattr(handlers, "train", train)
    monkeypatch.setattr(training, "ensure_weights", lambda model_id, directory: directory / f"{model_id}.pt")
    monkeypatch.setattr(get_settings(), "device", "cpu", raising=False)
    project, version = _exported_version(tmp_path, "CPU Is Honoured")

    with run_jobs_inline():
        _start(project, version)

    assert seen["device"] == "cpu"


# --- the splits a small dataset actually produces -----------------------------


def _splits(export_dir: Path, train: int, val: int, test: int) -> None:
    for split, count in (("train", train), ("val", val), ("test", test)):
        directory = export_dir / "images" / split
        directory.mkdir(parents=True, exist_ok=True)
        for existing in directory.iterdir():
            existing.unlink()
        for i in range(count):
            (directory / f"{i}.jpg").write_bytes(b"jpeg")


def _export_dir(project: dict, version: dict) -> Path:
    return Path(project["workspace_path"]) / "exports" / f"v{version['version']}"


def test_training_uses_the_test_split_when_there_are_no_validation_images(tmp_path, models_dir, fake_training):
    """Four labelled frames came out as three train, none val, one
    test - and ultralytics refuses to start without a validation set.
    """
    project, version = _exported_version(tmp_path, "No Val Images")
    _splits(_export_dir(project, version), train=3, val=0, test=1)

    with run_jobs_inline():
        status, body = _start(project, version)

    assert status == 202, body
    yaml = (_export_dir(project, version) / "data.yaml").read_text(encoding="utf-8")
    assert "val: images/test" in yaml


def test_validating_on_another_split_is_recorded_rather_than_hidden(tmp_path, models_dir, fake_training):
    """Nobody should read the resulting mAP as a real measure without
    being told what it was measured on."""
    project, version = _exported_version(tmp_path, "Substitution Is Recorded")
    _splits(_export_dir(project, version), train=3, val=0, test=1)

    with run_jobs_inline():
        _, body = _start(project, version)

    run = client.get(f"/training-runs/{body['run']['id']}").json()
    assert run["settings_json"]["validated_on"] == "test"
    assert "no validation images" in run["settings_json"]["note"]


def test_with_neither_val_nor_test_it_falls_back_to_train_and_says_so(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Only Train Images")
    _splits(_export_dir(project, version), train=2, val=0, test=0)

    with run_jobs_inline():
        _, body = _start(project, version)

    run = client.get(f"/training-runs/{body['run']['id']}").json()
    assert run["settings_json"]["validated_on"] == "train"
    assert "not a measure of anything" in run["settings_json"]["note"]


def test_a_real_validation_split_is_left_alone(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Real Val Split")
    _splits(_export_dir(project, version), train=8, val=2, test=1)

    with run_jobs_inline():
        _, body = _start(project, version)

    yaml = (_export_dir(project, version) / "data.yaml").read_text(encoding="utf-8")
    assert "val: images/val" in yaml
    run = client.get(f"/training-runs/{body['run']['id']}").json()
    assert "note" not in run["settings_json"]


def test_an_export_with_no_training_images_is_refused(tmp_path, models_dir, fake_training):
    """There is nothing to train on, and an hour of GPU time would
    prove it."""
    project, version = _exported_version(tmp_path, "Nothing To Train On")
    _splits(_export_dir(project, version), train=0, val=0, test=0)

    status, body = _start(project, version)

    assert status == 400
    assert "no training images" in body["message"].lower()


# --- clearing old runs off the list ------------------------------------------


def test_a_finished_run_can_be_taken_off_the_list(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Forget One Run")
    with run_jobs_inline():
        _, body = _start(project, version)
    run_id = body["run"]["id"]

    removed = client.delete(f"/training-runs/{run_id}")

    assert removed.status_code == 204
    assert run_id not in [r["id"] for r in client.get(f"/projects/{project['id']}/training-runs").json()]


def test_forgetting_a_run_keeps_the_model_it_trained(tmp_path, models_dir, fake_training):
    """Clearing an old failure off a screen should not delete a model
    that may be in use."""
    project, version = _exported_version(tmp_path, "Forget Keeps The Model")
    with run_jobs_inline():
        _, body = _start(project, version)
    model_id = client.get(f"/training-runs/{body['run']['id']}").json()["output_model_id"]

    client.delete(f"/training-runs/{body['run']['id']}")

    assert (models_dir / f"{model_id}.pt").is_file()
    assert model_id in [m["id"] for m in client.get("/models").json()]


def test_a_run_that_is_still_going_cannot_be_forgotten(tmp_path, models_dir, fake_training):
    """The row is what holds the GPU lock. Deleting it while the
    process carries on would let a second run start."""
    project, version = _exported_version(tmp_path, "Cannot Forget A Live Run")
    _, body = _start(project, version)

    refused = client.delete(f"/training-runs/{body['run']['id']}")

    assert refused.status_code == 409
    assert "cancel it first" in refused.json()["message"].lower()


def test_all_finished_runs_can_be_cleared_at_once(tmp_path, models_dir, fake_training):
    """Three failures from three attempts at the same bug is a list
    nobody wants to clear one at a time."""
    project, version = _exported_version(tmp_path, "Clear Them All")
    for attempt in range(3):
        with run_jobs_inline():
            status, body = _start(project, version)
        assert status == 202, f"attempt {attempt}: {body}"

    cleared = client.delete(f"/projects/{project['id']}/training-runs")

    assert cleared.status_code == 200
    assert cleared.json()["forgotten"] == 3
    assert client.get(f"/projects/{project['id']}/training-runs").json() == []


def test_clearing_leaves_a_run_that_is_still_going(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Clearing Spares The Live One")
    with run_jobs_inline():
        _start(project, version)
    _, live = _start(project, version)

    client.delete(f"/projects/{project['id']}/training-runs")

    remaining = [r["id"] for r in client.get(f"/projects/{project['id']}/training-runs").json()]
    assert remaining == [live["run"]["id"]]


# --- memory -------------------------------------------------------------------


class _RecordingYolo:
    """Stands in for ultralytics.YOLO and remembers what train() was given."""

    trained_with: dict = {}

    def __init__(self, weights):
        pass

    def add_callback(self, event, callback):
        pass

    def train(self, **kwargs):
        type(self).trained_with = kwargs
        best = Path(kwargs["project"]) / kwargs["name"] / "weights" / "best.pt"
        best.parent.mkdir(parents=True, exist_ok=True)
        best.write_bytes(b"trained weights")
        return None


def _train_recording(monkeypatch, tmp_path):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=_RecordingYolo))
    training.train_with_ultralytics(
        weights=tmp_path / "base.pt",
        data_yaml=tmp_path / "data.yaml",
        output_dir=tmp_path / "out",
        epochs=1,
        image_size=640,
        device="cpu",
        on_epoch=lambda progress: None,
    )
    return _RecordingYolo.trained_with


def test_training_stays_within_the_memory_the_machine_has(monkeypatch, tmp_path):
    """Left to its defaults ultralytics starts eight dataloader workers,
    each holding about half a gigabyte, and the kernel killed a medium
    run at epoch 13 on a 15 GB server it shares with another system.
    Workers and batch are set, never left to ultralytics."""
    trained_with = _train_recording(monkeypatch, tmp_path)

    assert trained_with["workers"] == 2
    assert trained_with["batch"] == 8


def test_workers_and_batch_can_be_tuned_per_machine(monkeypatch, tmp_path):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "train_workers", 0, raising=False)
    monkeypatch.setattr(get_settings(), "train_batch", 4, raising=False)

    trained_with = _train_recording(monkeypatch, tmp_path)

    assert trained_with["workers"] == 0
    assert trained_with["batch"] == 4


# --- training a plate detector ----------------------------------------------


def test_a_plate_detector_is_never_shown_mirrored_plates():
    plate = training.augmentation_for(["vehicle_plate"])
    assert plate["fliplr"] == 0.0
    assert plate["degrees"] <= 5
    assert plate["patience"] > 0


def test_anything_else_keeps_the_ultralytics_defaults():
    """A mirrored car is still a car."""
    assert training.augmentation_for(["car", "bus", "truck"]) == {}


def test_the_augmentation_reaches_ultralytics(monkeypatch, tmp_path):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=_RecordingYolo))
    training.train_with_ultralytics(
        weights=tmp_path / "base.pt",
        data_yaml=tmp_path / "data.yaml",
        output_dir=tmp_path / "out",
        epochs=1,
        image_size=960,
        device="cpu",
        on_epoch=lambda progress: None,
        augmentation={"fliplr": 0.0, "patience": 30},
    )
    assert _RecordingYolo.trained_with["fliplr"] == 0.0
    assert _RecordingYolo.trained_with["patience"] == 30
    assert _RecordingYolo.trained_with["imgsz"] == 960


def test_a_run_records_and_uses_what_it_was_trained_with(tmp_path, models_dir, fake_training):
    """This project's classes are vehicles, so it keeps the defaults - and
    says so on the run, where it can be read back later."""
    project, version = _exported_version(tmp_path, "Records Augmentation")

    with run_jobs_inline():
        status, body = _start(project, version)

    assert status == 202, body
    run = client.get(f"/training-runs/{body['run']['id']}").json()
    assert run["settings_json"]["augmentation"] == {}
    assert fake_training[-1]["augmentation"] is None


def test_the_classes_are_read_from_what_was_exported(tmp_path):
    """The export's own snapshot, not today's class list: a class
    renamed since must not change how that version is trained."""
    import json

    (tmp_path / "manifest.json").write_text(
        json.dumps({"config": {"classes": [{"id": 1, "name": "vehicle_plate"}]}}), encoding="utf-8"
    )
    with SessionLocal() as db:
        names = training._exported_class_names(db, None, tmp_path)  # type: ignore[arg-type]
    assert names == ["vehicle_plate"]
    assert training.augmentation_for(names)["fliplr"] == 0.0



# --- testing what was trained -------------------------------------------------


def _manifest_with(tmp_path, items):
    import json

    (tmp_path / "manifest.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    return tmp_path


def _item(name, split="test", night=None, boxes=1):
    attributes = {} if night is None else {"night": night}
    return {
        "image_path": f"images/{split}/{name}.jpg",
        "split": split,
        "objects": [{"class_name": "plate", "attributes": attributes} for _ in range(boxes)],
    }


def test_the_test_images_are_split_by_night_when_any_are_marked():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        export = _manifest_with(
            Path(tmp),
            [_item("a"), _item("b", night=True), _item("c", night=False), _item("bg", boxes=0), _item("t", split="train")],
        )
        subsets = training.test_subsets(export)

    assert {name: len(paths) for name, paths in subsets.items()} == {"all": 4, "night": 1, "day": 2}


def test_without_night_marks_there_is_only_the_whole():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        subsets = training.test_subsets(_manifest_with(Path(tmp), [_item("a"), _item("b")]))
    assert list(subsets) == ["all"]


def test_a_finished_run_carries_its_test_scores(tmp_path, models_dir, fake_training):
    project, version = _exported_version(tmp_path, "Test Scores")

    with run_jobs_inline():
        status, body = _start(project, version)

    run = client.get(f"/training-runs/{body['run']['id']}").json()
    assert run["status"] == "completed"
    metrics = run["settings_json"]["test_metrics"]
    # The tiny export may or may not have test images; either is said.
    assert ("all" in metrics and metrics["all"]["recall"] == 0.95) or "note" in metrics


def test_a_test_that_fails_does_not_cost_the_trained_model(tmp_path, models_dir, fake_training, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(training, "test_subsets", lambda export_dir: {"all": [Path("x.jpg")]})
    monkeypatch.setattr(handlers, "evaluate", broken)
    project, version = _exported_version(tmp_path, "Test Fails")

    with run_jobs_inline():
        status, body = _start(project, version)

    run = client.get(f"/training-runs/{body['run']['id']}").json()
    assert run["status"] == "completed"
    assert run["output_model_id"]
    assert "Testing failed" in run["settings_json"]["test_metrics"]["error"]


def test_each_subset_is_tested_on_its_own_images(tmp_path):
    import yaml

    export = _manifest_with(tmp_path, [_item("a", night=False), _item("b", night=True), _item("c", night=True)])
    (tmp_path / "data.yaml").write_text(yaml.safe_dump({"path": ".", "train": "images/train", "names": {0: "plate"}}))
    seen: dict[str, list[str]] = {}

    def evaluate(weights, data_yaml, image_size, device):
        config = yaml.safe_load(Path(data_yaml).read_text())
        seen[Path(data_yaml).name] = Path(config["test"]).read_text().split()
        return {"precision": 1.0, "recall": 1.0, "map50": 1.0, "map50_95": 1.0}

    results = training.evaluate_on_test(evaluate, Path("best.pt"), export, tmp_path / "data.yaml", 960, "cpu")

    assert {name: r["images"] for name, r in results.items()} == {"all": 3, "night": 2, "day": 1}
    assert len(seen["data_test_night.yaml"]) == 2
    assert seen["data_test_day.yaml"][0].endswith("a.jpg")
