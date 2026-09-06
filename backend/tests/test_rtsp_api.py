import time

import numpy as np
from fastapi.testclient import TestClient

from app.api.rtsp import get_rtsp_connection_provider
from app.main import app
from app.ml.factory import get_default_detector
from tests.stub_detector import StubDetector

client = TestClient(app)


class _InstantFrameConnection:
    """Opens immediately and yields frames with a tiny pacing delay -
    used so API tests never wait on a real (slow) network attempt."""

    def __init__(self, url: str) -> None:
        self._url = url

    def open(self) -> bool:
        return True

    def read(self) -> np.ndarray | None:
        time.sleep(0.005)
        return np.full((48, 64, 3), fill_value=128, dtype=np.uint8)

    def release(self) -> None:
        pass


class _NeverOpensConnection:
    def __init__(self, url: str) -> None:
        self._url = url

    def open(self) -> bool:
        return False

    def read(self) -> np.ndarray | None:
        return None

    def release(self) -> None:
        pass


def _create_project(name: str) -> dict:
    return client.post("/projects", json={"name": name}).json()


def test_start_creates_source_and_run_with_sentinel_fields(tmp_path):
    project = _create_project("RTSP Start Project")
    app.dependency_overrides[get_default_detector] = lambda: StubDetector()
    app.dependency_overrides[get_rtsp_connection_provider] = lambda: (lambda url: _NeverOpensConnection(url))
    try:
        response = client.post(
            f"/projects/{project['id']}/sources/rtsp/start",
            json={"rtsp_url": "rtsp://camera.example/stream", "expected_fps": 8.0, "buffer_maxlen": 100},
        )
        assert response.status_code == 201
        body = response.json()

        assert body["source"]["type"] == "rtsp"
        assert body["source"]["path_or_uri"] == "rtsp://camera.example/stream"
        assert body["source"]["fps"] == 8.0
        assert body["source"]["frame_count"] == 0
        assert body["source"]["duration_ms"] == 0
        assert body["run"]["status"] == "running"
        assert body["run"]["detector_version"] == "stub-detector-v1"

        # The default reconnect backoff (5 attempts, up to ~31s total)
        # is deliberately not fully exhausted here - a short poll
        # window is enough to prove reconnection is genuinely being
        # attempted (not that it eventually gives up, which the
        # service-level test_rtsp_session.py already covers directly
        # with a fast-backoff config).
        run_id = body["run"]["id"]
        deadline = time.monotonic() + 3
        status = None
        while time.monotonic() < deadline:
            status = client.get(f"/processing-runs/{run_id}/rtsp/status").json()
            if status["reconnect_attempts"] >= 1:
                break
            time.sleep(0.05)

        assert status is not None
        assert status["connected"] is False
        assert status["reconnect_attempts"] >= 1
        assert status["stopped"] is False
        assert status["frames_captured"] == 0

        client.post(f"/processing-runs/{run_id}/rtsp/stop")
    finally:
        app.dependency_overrides.pop(get_default_detector, None)
        app.dependency_overrides.pop(get_rtsp_connection_provider, None)


def test_full_start_status_stop_lifecycle(tmp_path):
    project = _create_project("RTSP Lifecycle Project")
    app.dependency_overrides[get_default_detector] = lambda: StubDetector()
    app.dependency_overrides[get_rtsp_connection_provider] = lambda: (lambda url: _InstantFrameConnection(url))
    try:
        started = client.post(
            f"/projects/{project['id']}/sources/rtsp/start",
            json={"rtsp_url": "rtsp://camera.example/live", "expected_fps": 10.0},
        ).json()
        run_id = started["run"]["id"]

        # Let it capture a handful of frames.
        time.sleep(0.3)
        mid_status = client.get(f"/processing-runs/{run_id}/rtsp/status").json()
        assert mid_status["connected"] is True
        assert mid_status["frames_captured"] > 0
        assert mid_status["stopped"] is False

        stop_response = client.post(f"/processing-runs/{run_id}/rtsp/stop")
        assert stop_response.status_code == 200

        deadline = time.monotonic() + 5
        final_status = mid_status
        while time.monotonic() < deadline:
            final_status = client.get(f"/processing-runs/{run_id}/rtsp/status").json()
            if final_status["stopped"]:
                break
            time.sleep(0.05)

        assert final_status["stopped"] is True
        assert final_status["connected"] is False
        assert final_status["frames_captured"] >= mid_status["frames_captured"]

        run_detail = client.get(f"/dataset-versions/{run_id}")  # wrong resource on purpose: 404 not 500
        assert run_detail.status_code == 404
    finally:
        app.dependency_overrides.pop(get_default_detector, None)
        app.dependency_overrides.pop(get_rtsp_connection_provider, None)


def test_status_for_unknown_run_returns_404():
    response = client.get("/processing-runs/does-not-exist/rtsp/status")
    assert response.status_code == 404


def test_stop_for_unknown_run_returns_404():
    response = client.post("/processing-runs/does-not-exist/rtsp/stop")
    assert response.status_code == 404


def test_start_for_unknown_project_returns_404():
    response = client.post(
        "/projects/does-not-exist/sources/rtsp/start",
        json={"rtsp_url": "rtsp://camera.example/stream"},
    )
    assert response.status_code == 404
