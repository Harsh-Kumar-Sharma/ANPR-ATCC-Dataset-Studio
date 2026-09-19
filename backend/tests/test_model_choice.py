"""Choosing which model detects.

The detector was one hard-coded file behind a cache, so there was no
way to try a bigger model on a hard clip and no way at all to use one
you trained yourself.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.errors import NotFoundError
from app.main import app
from app.ml import models as registry
from app.ml.factory import get_detector_provider
from app.ml.models import BadModelIdError, DEFAULT_MODEL_ID
from app.ml.weights import WeightsUnavailableError, ensure_weights, ensure_weights_for
from app.ml.types import Detection
from tests.job_execution import process_source_sync
from tests.video_factory import create_synthetic_video

client = TestClient(app)

WIDTH, HEIGHT = 128, 96


class _StubDetector:
    def __init__(self, model_id: str = "stub") -> None:
        self.model_version = model_id
        self.class_names = {0: "car"}

    def detect(self, frame):
        return [Detection(bbox_xyxy=(20.0, 20.0, 80.0, 70.0), class_id=0, confidence=0.9)]


# --- the registry -------------------------------------------------------------


def test_both_builtin_models_are_offered(tmp_path):
    """Two, differing in the way that matters: speed against accuracy."""
    ids = [m.id for m in registry.list_models(tmp_path)]

    assert ids == ["yolo26n", "yolo26s"]


def test_a_builtin_is_offered_before_its_weights_are_downloaded(tmp_path):
    """"Not downloaded yet" is a state the first run resolves, not a
    reason to hide the option."""
    larger = [m for m in registry.list_models(tmp_path) if m.id == "yolo26s"][0]

    assert larger.present is False
    assert larger.bytes == 0


def test_a_model_you_put_in_the_directory_is_offered_too(tmp_path):
    (tmp_path / "gantry-v3.pt").write_bytes(b"weights")

    mine = [m for m in registry.list_models(tmp_path) if m.id == "gantry-v3"][0]

    assert mine.kind == "custom"
    assert mine.present is True
    assert mine.bytes == len(b"weights")


def test_a_builtin_file_is_not_also_listed_as_a_custom_model(tmp_path):
    (tmp_path / "yolo26n.pt").write_bytes(b"weights")

    ids = [m.id for m in registry.list_models(tmp_path)]

    assert ids.count("yolo26n") == 1


def test_an_unknown_model_is_refused_rather_than_quietly_defaulted(tmp_path):
    """Detecting with a different model than the one asked for, and
    saying nothing, is how two runs become incomparable."""
    with pytest.raises(NotFoundError):
        registry.get_model("no-such-model", tmp_path)


@pytest.mark.parametrize("bad", ["../secrets", "a/b", "", "..", "C:\\weights"])
def test_an_id_that_could_name_a_file_is_refused(bad, tmp_path):
    with pytest.raises(BadModelIdError):
        registry.get_model(bad, tmp_path)


# --- getting the weights there ------------------------------------------------


def test_weights_already_on_disk_are_not_fetched_again(tmp_path):
    (tmp_path / "yolo26n.pt").write_bytes(b"weights")
    calls = []

    path = ensure_weights("yolo26n", tmp_path, fetch=lambda f, d: calls.append(f) or (d / f))

    assert path == tmp_path / "yolo26n.pt"
    assert calls == []


def test_a_missing_builtin_is_fetched_once_and_the_wait_is_reported(tmp_path):
    """A first run on a model that is not on disk otherwise sits silent
    for a minute with no sign that anything is happening."""
    said = []

    def fetch(weights_file, directory):
        (directory / weights_file).write_bytes(b"downloaded")
        return directory / weights_file

    path = ensure_weights("yolo26s", tmp_path, report=said.append, fetch=fetch)

    assert path.read_bytes() == b"downloaded"
    assert any("first use" in message for message in said)


def test_a_failed_download_says_what_to_do_about_it(tmp_path):
    def fetch(weights_file, directory):
        raise OSError("no route to host")

    with pytest.raises(WeightsUnavailableError) as raised:
        ensure_weights("yolo26s", tmp_path, fetch=fetch)

    assert "yolo26s.pt" in str(raised.value)
    assert str(tmp_path) in str(raised.value)


def test_a_download_that_leaves_no_file_is_not_called_a_success(tmp_path):
    with pytest.raises(WeightsUnavailableError):
        ensure_weights("yolo26s", tmp_path, fetch=lambda f, d: d / f)


def test_a_custom_model_that_has_gone_missing_is_not_downloaded(tmp_path):
    """There is nowhere to fetch it from, and inventing a download for
    a file the user was supposed to provide turns a clear "it is not
    there" into a confusing network error."""
    weights = tmp_path / "mine.pt"
    weights.write_bytes(b"weights")
    model = registry.get_model("mine", tmp_path)
    weights.unlink()  # deleted between being listed and being loaded

    def fetch(weights_file, directory):
        raise AssertionError("a custom model must never be downloaded")

    with pytest.raises(WeightsUnavailableError) as raised:
        ensure_weights_for(model, tmp_path, fetch=fetch)

    assert "mine" in str(raised.value)


# --- the endpoint -------------------------------------------------------------


def test_the_api_lists_what_can_detect():
    response = client.get("/models")

    assert response.status_code == 200, response.text
    ids = [m["id"] for m in response.json()]
    assert DEFAULT_MODEL_ID in ids
    assert "yolo26s" in ids


def test_every_listed_model_says_whether_it_is_here_and_how_big():
    rows = client.get("/models").json()

    for row in rows:
        assert set(row) >= {"id", "label", "kind", "present", "bytes", "note"}


# --- running with a chosen model ----------------------------------------------


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _source(project: dict, tmp_path) -> dict:
    video = create_synthetic_video(tmp_path / "clip.mp4", frame_count=4, fps=10.0, width=WIDTH, height=HEIGHT)
    return client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()


def test_a_run_records_the_model_that_was_asked_for(tmp_path):
    project = _project("Model On The Run")
    source = _source(project, tmp_path)

    submitted = process_source_sync(
        client, project["id"], source["id"], _StubDetector("yolo26s"), target_fps=10.0
    )

    run = client.get(f"/processing-runs/{submitted['run_id']}").json()
    assert run["detector_version"] == "yolo26s"


def test_asking_for_a_model_that_does_not_exist_is_refused_at_once(tmp_path):
    """Answering a typo two minutes later through a failed job is a
    poor way to report a typo."""
    project = _project("Unknown Model")
    source = _source(project, tmp_path)

    response = client.post(
        f"/projects/{project['id']}/sources/{source['id']}/process",
        json={"sampling_config": {"target_fps": 10.0}, "model_id": "no-such-model"},
    )

    assert response.status_code == 404
    runs = client.get(f"/projects/{project['id']}/sources/{source['id']}/processing-runs")
    if runs.status_code == 200:
        assert runs.json() == []


def test_the_evaluation_report_says_which_model_ran(tmp_path):
    """Comparing two runs is the point of being able to choose one."""
    project = _project("Report Names The Model")
    source = _source(project, tmp_path)
    submitted = process_source_sync(client, project["id"], source["id"], _StubDetector("yolo26s"), target_fps=10.0)

    report = client.get(f"/processing-runs/{submitted['run_id']}/evaluation").json()

    assert report["detector_version"] == "yolo26s"


# --- the live stream gets the same choice -------------------------------------


class _NeverOpensConnection:
    def __init__(self, url: str) -> None:
        self._url = url

    def open(self) -> bool:
        return False

    def read(self):
        return None

    def release(self) -> None:
        pass


def test_a_live_session_is_started_with_the_chosen_model():
    """A live session is where a model you trained earns its keep."""
    asked = []

    def provider(model_id: str):
        asked.append(model_id)
        return _StubDetector(model_id)

    from app.api.rtsp import get_rtsp_connection_provider

    app.dependency_overrides[get_detector_provider] = lambda: provider
    app.dependency_overrides[get_rtsp_connection_provider] = lambda: (lambda url: _NeverOpensConnection(url))
    try:
        project = _project("Live With A Model")
        response = client.post(
            f"/projects/{project['id']}/sources/rtsp/start",
            json={"rtsp_url": "rtsp://camera/ch1", "expected_fps": 5.0, "model_id": "yolo26s"},
        )
        assert response.status_code == 201, response.text
        assert asked == ["yolo26s"]
        assert response.json()["run"]["detector_version"] == "yolo26s"
        client.post(f"/processing-runs/{response.json()['run']['id']}/rtsp/stop")
    finally:
        app.dependency_overrides.pop(get_detector_provider, None)
        app.dependency_overrides.pop(get_rtsp_connection_provider, None)


def test_a_live_session_without_a_choice_uses_the_default():
    asked = []

    from app.api.rtsp import get_rtsp_connection_provider

    app.dependency_overrides[get_detector_provider] = lambda: (
        lambda model_id: asked.append(model_id) or _StubDetector(model_id)
    )
    app.dependency_overrides[get_rtsp_connection_provider] = lambda: (lambda url: _NeverOpensConnection(url))
    try:
        project = _project("Live Default Model")
        response = client.post(
            f"/projects/{project['id']}/sources/rtsp/start",
            json={"rtsp_url": "rtsp://camera/ch1", "expected_fps": 5.0},
        )
        assert response.status_code == 201, response.text
        assert asked == [DEFAULT_MODEL_ID]
        client.post(f"/processing-runs/{response.json()['run']['id']}/rtsp/stop")
    finally:
        app.dependency_overrides.pop(get_detector_provider, None)
        app.dependency_overrides.pop(get_rtsp_connection_provider, None)


# --- the cache ----------------------------------------------------------------


def test_switching_model_does_not_reload_the_one_already_in_memory(monkeypatch):
    """The cache is keyed by model, not by "the detector": going back
    to the first model must not pay for loading it twice."""
    from app.ml import factory

    loaded = []

    class _Loaded:
        def __init__(self, weights, name=None, device=None):
            loaded.append(name)
            self.model_version = name

    monkeypatch.setattr(factory, "YoloDetector", _Loaded)
    monkeypatch.setattr(factory, "ensure_weights", lambda model_id, directory: directory / f"{model_id}.pt")
    factory.get_detector.cache_clear()
    try:
        factory.get_detector("yolo26n")
        factory.get_detector("yolo26s")
        factory.get_detector("yolo26n")
    finally:
        factory.get_detector.cache_clear()

    assert loaded == ["yolo26n", "yolo26s"]
