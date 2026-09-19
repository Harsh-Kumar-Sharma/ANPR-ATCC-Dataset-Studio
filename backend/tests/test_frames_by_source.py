"""Frames belong to a source, and the queue has to know it.

With three sources the queue was one list ordered by source then frame
index, so you could not tell whose frames you were looking at or work
one clip at a time. That is the complaint this answers.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.ml.types import Detection
from tests.job_execution import process_source_sync
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


def _source(project: dict, tmp_path, name: str, frame_count: int = 6) -> dict:
    video = create_synthetic_video(
        tmp_path / f"{name}.mp4", frame_count=frame_count, fps=10.0, width=WIDTH, height=HEIGHT
    )
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    process_source_sync(client, project["id"], source["id"], _OneCarDetector(), target_fps=10.0)
    return source


def _frames(project: dict, source: dict | None = None) -> list[dict]:
    params = {"source_id": source["id"]} if source else {}
    response = client.get(f"/projects/{project['id']}/frames", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _progress(project: dict, source: dict | None = None) -> dict:
    params = {"source_id": source["id"]} if source else {}
    response = client.get(f"/projects/{project['id']}/frames/progress", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _label(frame: dict):
    response = client.put(
        f"/frames/{frame['id']}/annotations",
        json={"annotations": [{"class_id": CAR_CLASS, "bbox_json": [10.0, 10.0, 70.0, 60.0]}]},
    )
    assert response.status_code == 200, response.text


# --- narrowing the queue ------------------------------------------------------


def test_the_queue_can_be_narrowed_to_one_source(tmp_path):
    project = _project("Narrow Queue")
    first = _source(project, tmp_path / "a", "first", frame_count=6)
    second = _source(project, tmp_path / "b", "second", frame_count=4)

    assert len(_frames(project)) == len(_frames(project, first)) + len(_frames(project, second))
    assert {f["source_id"] for f in _frames(project, first)} == {first["id"]}
    assert {f["source_id"] for f in _frames(project, second)} == {second["id"]}


def test_the_progress_counts_respect_the_same_filter(tmp_path):
    """A progress line that counts the whole project while the list
    shows one source is worse than no progress line."""
    project = _project("Narrow Progress")
    first = _source(project, tmp_path / "a", "first", frame_count=6)
    second = _source(project, tmp_path / "b", "second", frame_count=4)
    _label(_frames(project, first)[0])

    whole = _progress(project)
    just_first = _progress(project, first)
    just_second = _progress(project, second)

    assert just_first["total"] + just_second["total"] == whole["total"]
    assert just_first["labeled"] == 1
    assert just_second["labeled"] == 0


def test_narrowing_combines_with_the_status_filter(tmp_path):
    project = _project("Narrow And Status")
    first = _source(project, tmp_path / "a", "first")
    _label(_frames(project, first)[0])

    response = client.get(
        f"/projects/{project['id']}/frames", params={"source_id": first["id"], "status": "labeled"}
    )

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_an_unknown_source_is_refused_rather_than_answered_with_nothing(tmp_path):
    """An empty list reads identically to "this source has no frames",
    which is the wrong thing to tell someone who mistyped an id."""
    project = _project("Unknown Source Filter")
    _source(project, tmp_path, "real")

    response = client.get(f"/projects/{project['id']}/frames", params={"source_id": "not-a-source"})

    assert response.status_code == 404


def test_a_source_from_another_project_is_refused(tmp_path):
    mine = _project("Mine")
    theirs = _project("Theirs")
    _source(mine, tmp_path / "mine", "mine")
    other = _source(theirs, tmp_path / "theirs", "theirs")

    response = client.get(f"/projects/{mine['id']}/frames", params={"source_id": other["id"]})

    assert response.status_code == 404


# --- the picker's own numbers -------------------------------------------------


def test_each_source_reports_its_own_progress(tmp_path):
    """So the picker can say which clip still needs work without asking
    once per source."""
    project = _project("Per Source Progress")
    first = _source(project, tmp_path / "a", "first", frame_count=6)
    second = _source(project, tmp_path / "b", "second", frame_count=4)
    _label(_frames(project, first)[0])

    response = client.get(f"/projects/{project['id']}/frames/by-source")

    assert response.status_code == 200, response.text
    by_id = {row["source_id"]: row for row in response.json()}
    assert by_id[first["id"]]["labeled"] == 1
    assert by_id[first["id"]]["total"] == len(_frames(project, first))
    assert by_id[second["id"]]["labeled"] == 0


def test_the_per_source_list_names_each_source(tmp_path):
    """The picker needs something to show, and the id is not it."""
    project = _project("Per Source Names")
    source = _source(project, tmp_path, "gantry_north")

    rows = client.get(f"/projects/{project['id']}/frames/by-source").json()

    assert rows[0]["path_or_uri"].endswith("gantry_north.mp4")
    assert rows[0]["type"] == "video"


def test_a_source_with_no_frames_still_appears(tmp_path):
    """Otherwise it vanishes from the picker and there is no way to see
    that it has nothing to label yet."""
    project = _project("Empty Source In Picker")
    video = create_synthetic_video(tmp_path / "empty.mp4", frame_count=4, fps=10.0, width=WIDTH, height=HEIGHT)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()

    rows = client.get(f"/projects/{project['id']}/frames/by-source").json()

    by_id = {row["source_id"]: row for row in rows}
    assert by_id[source["id"]]["total"] == 0


def test_the_per_source_list_is_ordered_with_the_most_work_left_first(tmp_path):
    """A labeller opening the tab wants the clip that still needs
    doing, not whichever was imported first."""
    project = _project("Most Work First")
    small = _source(project, tmp_path / "a", "small", frame_count=2)
    large = _source(project, tmp_path / "b", "large", frame_count=8)

    rows = client.get(f"/projects/{project['id']}/frames/by-source").json()

    assert [row["source_id"] for row in rows][0] == large["id"]
    assert small["id"] in [row["source_id"] for row in rows]
