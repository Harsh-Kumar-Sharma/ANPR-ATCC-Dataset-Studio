"""Remembering a camera, so starting it again is one click.

Starting a live capture meant retyping the URL, the frame rate, the
model and the keep-frames settings every time - including right after
a Stop, which is the most likely moment to want the same camera back.
"""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.rtsp import get_rtsp_connection_provider
from app.main import app
from app.ml.factory import get_detector_provider

client = TestClient(app)

URL = "rtsp://admin:secret@10.0.0.4:9001/Streaming/channels/1"


class _StubDetector:
    model_version = "stub"
    class_names = {0: "car"}

    def detect(self, frame):
        return []


class _NeverOpensConnection:
    def __init__(self, url: str) -> None:
        self._url = url

    def open(self) -> bool:
        return False

    def read(self) -> np.ndarray | None:
        return None

    def release(self) -> None:
        pass


@pytest.fixture
def live():
    """A live start that never touches a camera or loads a model."""
    app.dependency_overrides[get_detector_provider] = lambda: (lambda model_id: _StubDetector())
    app.dependency_overrides[get_rtsp_connection_provider] = lambda: (lambda url: _NeverOpensConnection(url))
    yield
    app.dependency_overrides.pop(get_detector_provider, None)
    app.dependency_overrides.pop(get_rtsp_connection_provider, None)


def _project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def _start(project: dict, **over) -> dict:
    body = {"rtsp_url": URL, "expected_fps": 10.0, **over}
    response = client.post(f"/projects/{project['id']}/sources/rtsp/start", json=body)
    assert response.status_code == 201, response.text
    started = response.json()
    client.post(f"/processing-runs/{started['run']['id']}/rtsp/stop")
    return started


def _cameras(project: dict) -> list[dict]:
    response = client.get(f"/projects/{project['id']}/sources/rtsp/cameras")
    assert response.status_code == 200, response.text
    return response.json()


# --- remembering --------------------------------------------------------------


def test_starting_a_camera_remembers_how_it_was_started(live):
    """No Save button: a button the user has to remember to press is
    one they will not press."""
    project = _project("Remembers The Camera")

    _start(project, expected_fps=25.0, model_id=None, keep_frames=True, keep_every=15)

    camera = _cameras(project)[0]
    assert camera["rtsp_url"] == URL
    assert camera["expected_fps"] == 25.0
    assert camera["keep_frames"] is True
    assert camera["keep_every"] == 15


def test_a_project_with_no_history_offers_nothing(live):
    project = _project("Nothing Remembered")

    assert _cameras(project) == []


def test_starting_the_same_camera_again_updates_it_rather_than_copying_it(live):
    """Two rows for one camera is two entries with the same name and
    different settings, which is worse than not remembering."""
    project = _project("No Duplicate Cameras")
    _start(project, expected_fps=10.0, keep_frames=False)

    _start(project, expected_fps=30.0, keep_frames=True, keep_every=5)

    cameras = _cameras(project)
    assert len(cameras) == 1
    assert cameras[0]["expected_fps"] == 30.0
    assert cameras[0]["keep_frames"] is True
    assert cameras[0]["keep_every"] == 5


def test_two_cameras_are_offered_most_recent_first(live):
    """The one stopped a minute ago is the one most likely wanted."""
    project = _project("Two Cameras")
    other = "rtsp://admin:secret@10.0.0.5:9001/Streaming/channels/2"
    _start(project, rtsp_url=other)
    _start(project, rtsp_url=URL)

    assert [c["rtsp_url"] for c in _cameras(project)] == [URL, other]


def test_cameras_belong_to_their_project(live):
    project = _project("Mine")
    other = _project("Theirs")
    _start(project)

    assert _cameras(other) == []


def test_the_model_is_remembered_with_the_camera(live):
    """Watching a camera with a model you trained is part of the
    configuration, not a separate thing to re-pick."""
    project = _project("Remembers The Model")

    _start(project, model_id="yolo26s")

    assert _cameras(project)[0]["model_id"] == "yolo26s"


# --- forgetting ---------------------------------------------------------------


def test_a_camera_can_be_forgotten(live):
    project = _project("Forget A Camera")
    _start(project)
    camera_id = _cameras(project)[0]["id"]

    removed = client.delete(f"/projects/{project['id']}/sources/rtsp/cameras/{camera_id}")

    assert removed.status_code == 204
    assert _cameras(project) == []


def test_forgetting_a_camera_keeps_what_it_captured(live):
    """Forgetting a shortcut is not deleting footage, and conflating
    the two would be a nasty surprise."""
    project = _project("Forget Keeps Footage")
    _start(project)
    before = len(client.get(f"/projects/{project['id']}/sources").json())
    camera_id = _cameras(project)[0]["id"]

    client.delete(f"/projects/{project['id']}/sources/rtsp/cameras/{camera_id}")

    assert len(client.get(f"/projects/{project['id']}/sources").json()) == before


def test_forgetting_one_that_is_not_there_says_so(live):
    project = _project("Forget Unknown")

    response = client.delete(f"/projects/{project['id']}/sources/rtsp/cameras/not-a-camera")

    assert response.status_code == 404


def test_a_capture_still_starts_when_the_camera_cannot_be_remembered(live, monkeypatch):
    """Remembering is a convenience. A database that has not had this
    table's migration yet must not stop a camera from starting."""
    from sqlalchemy.exc import OperationalError

    from app.api import rtsp as rtsp_api

    def no_such_table(*args, **kwargs):
        raise OperationalError("no such table: live_cameras", {}, Exception())

    monkeypatch.setattr(rtsp_api.live_cameras, "remember", no_such_table)
    project = _project("Start Without Remembering")

    response = client.post(
        f"/projects/{project['id']}/sources/rtsp/start", json={"rtsp_url": URL, "expected_fps": 10.0}
    )

    assert response.status_code == 201, response.text
    client.post(f"/processing-runs/{response.json()['run']['id']}/rtsp/stop")
    # And the source and run it did create are still there.
    assert len(client.get(f"/projects/{project['id']}/sources").json()) == 1
