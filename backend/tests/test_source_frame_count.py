"""The Sources list counts frames that can be opened, as the queue does.

A live detection records a frame row whether or not its image was
written. On a full disk none were, and the list said "117 frame(s)
saved" beside a Label queue with nothing in it.
"""

from fastapi.testclient import TestClient

from app.db.models.frame import Frame
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)


def test_live_frames_without_an_image_are_not_counted_as_saved():
    project = client.post("/projects", json={"name": "Counted Frames"}).json()
    with SessionLocal() as db:
        source = Source(
            project_id=project["id"], type="rtsp", path_or_uri="rtsp://camera/ch1",
            fps=25, width=0, height=0, duration_ms=0, frame_count=0,
        )
        db.add(source)
        db.flush()
        db.add(Frame(source_id=source.id, frame_index=1, timestamp_ms=40, width=64, height=48, image_path="/w/f1.jpg"))
        for index in range(2, 6):
            db.add(Frame(source_id=source.id, frame_index=index, timestamp_ms=index * 40, width=64, height=48))
        db.commit()

    listed = client.get(f"/projects/{project['id']}/sources").json()
    assert listed[0]["stored_frames"] == 1

    queue = client.get(f"/projects/{project['id']}/frames/progress", params={"source_id": listed[0]["id"]}).json()
    assert queue["total"] == listed[0]["stored_frames"]
