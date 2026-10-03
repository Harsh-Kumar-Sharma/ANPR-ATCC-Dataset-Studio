"""Taking a model somewhere else - to production, most often.

The weights sat in the server's models directory with no way to them
from the app; getting one meant a shell on the server.
"""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)


def test_a_trained_model_downloads_as_its_own_named_file(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    (models / "yolo26m-v5.pt").write_bytes(b"trained weights")
    monkeypatch.setattr(get_settings(), "model_weights_dir", models, raising=False)

    response = client.get("/models/yolo26m-v5/weights")

    assert response.status_code == 200
    assert response.content == b"trained weights"
    assert 'filename="yolo26m-v5.pt"' in response.headers["content-disposition"]


def test_a_builtin_never_fetched_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "model_weights_dir", tmp_path / "empty", raising=False)

    response = client.get("/models/yolo26m/weights")

    assert response.status_code == 404
    assert "not been downloaded" in response.json()["message"]


def test_an_unknown_model_is_a_404(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "model_weights_dir", tmp_path / "empty", raising=False)
    assert client.get("/models/nope/weights").status_code == 404


def test_a_path_is_not_a_model_id(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "model_weights_dir", tmp_path / "models", raising=False)
    assert client.get("/models/..%2F..%2Fapp.db/weights").status_code in (400, 404)
