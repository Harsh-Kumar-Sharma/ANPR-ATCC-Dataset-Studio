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
        for run in db.query(TrainingRun).filter(TrainingRun.status.in_(training.UNFINISHED)):
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

    def train(weights, data_yaml, output_dir, epochs, image_size, device, on_epoch):
        calls.append(
            {
                "weights": Path(weights),
                "data_yaml": Path(data_yaml),
                "epochs": epochs,
                "image_size": image_size,
            }
        )
        on_epoch(TrainingProgress(epoch=1, epochs=epochs, loss=1.5, map50=0.20))
        on_epoch(TrainingProgress(epoch=2, epochs=epochs, loss=0.9, map50=0.55))
        best = Path(output_dir) / "run" / "weights" / "best.pt"
        best.parent.mkdir(parents=True, exist_ok=True)
        best.write_bytes(b"trained weights")
        return best

    monkeypatch.setattr(handlers, "train", train)
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

    def train(weights, data_yaml, output_dir, epochs, image_size, device, on_epoch):
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

    def train(weights, data_yaml, output_dir, epochs, image_size, device, on_epoch):
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
