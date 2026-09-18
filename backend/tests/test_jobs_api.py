import json

from fastapi.testclient import TestClient

from app.db.models import Job
from app.db.session import SessionLocal
from app.main import app
from app.services.jobs import runner
from tests.job_execution import deferred_jobs, process_source_sync
from tests.stub_detector import StubDetector
from tests.video_factory import create_synthetic_video

client = TestClient(app)


def _project_with_source(tmp_path, name: str):
    project = client.post("/projects", json={"name": name}).json()
    video = create_synthetic_video(tmp_path / f"{name}.mp4", frame_count=20, fps=10.0)
    source = client.post(f"/projects/{project['id']}/sources", json={"path": str(video)}).json()
    return project, source


def test_processing_returns_immediately_with_a_job_to_follow(tmp_path):
    """The whole point of the change: the request no longer blocks for the
    length of the run."""
    project, source = _project_with_source(tmp_path, "Async Process Project")

    with deferred_jobs() as launcher:
        response = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["job"]["type"] == "detect"
    assert body["job"]["status"] == "pending"
    assert body["run_id"]
    # A worker was asked for, and it was asked for this job.
    assert launcher.job_ids == [body["job"]["id"]]


def test_the_run_exists_before_any_work_happens(tmp_path):
    """The caller gets a run id up front so it can link to the run without
    waiting for a process to start."""
    project, source = _project_with_source(tmp_path, "Run Up Front Project")

    with deferred_jobs():
        body = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        ).json()

    run = client.get(f"/processing-runs/{body['run_id']}").json()
    assert run["status"] == "pending"
    assert run["sampling_config"]["target_fps"] == 5.0


def test_a_job_can_be_fetched_and_listed(tmp_path):
    project, source = _project_with_source(tmp_path, "Job Listing Project")
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    job_id = submitted["job"]["id"]

    fetched = client.get(f"/jobs/{job_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "succeeded"
    assert fetched.json()["progress"] == 1.0

    listed = client.get("/jobs", params={"project_id": project["id"]}).json()
    assert [j["id"] for j in listed] == [job_id]


def test_jobs_can_be_filtered_by_status_and_type(tmp_path):
    project, source = _project_with_source(tmp_path, "Job Filter Project")
    process_source_sync(client, project["id"], source["id"], StubDetector())

    assert client.get("/jobs", params={"project_id": project["id"], "status": "succeeded"}).json()
    assert client.get("/jobs", params={"project_id": project["id"], "status": "failed"}).json() == []
    assert client.get("/jobs", params={"project_id": project["id"], "type": "train"}).json() == []


def test_jobs_are_listed_newest_first(tmp_path):
    project, source = _project_with_source(tmp_path, "Job Order Project")
    first = process_source_sync(client, project["id"], source["id"], StubDetector())["job"]["id"]
    second = process_source_sync(client, project["id"], source["id"], StubDetector())["job"]["id"]

    listed = client.get("/jobs", params={"project_id": project["id"]}).json()
    assert [j["id"] for j in listed] == [second, first]


def test_a_finished_job_reports_what_it_produced(tmp_path):
    project, source = _project_with_source(tmp_path, "Job Result Project")
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())

    job = client.get(f"/jobs/{submitted['job']['id']}").json()
    assert job["result_json"]["run_id"] == submitted["run_id"]


def test_an_unknown_job_returns_404():
    response = client.get("/jobs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_live_progress_is_read_from_the_workers_file(tmp_path):
    """While a job runs, the row is stale by design - the worker reports
    into a file so it never contends for the database write lock."""
    project, source = _project_with_source(tmp_path, "Live Progress Project")

    with deferred_jobs():
        body = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        ).json()

    job_id = body["job"]["id"]
    with SessionLocal() as db:
        db.get(Job, job_id).status = "running"
        db.commit()

    runner.progress_path(job_id).parent.mkdir(parents=True, exist_ok=True)
    from app.services.jobs.progress import write_progress

    write_progress(runner.progress_path(job_id), fraction=0.33, message="Frame 33 of 100")

    job = client.get(f"/jobs/{job_id}").json()
    assert job["progress"] == 0.33
    assert job["progress_message"] == "Frame 33 of 100"


def test_progress_stream_ends_when_the_job_is_finished(tmp_path):
    """A client that subscribes after the fact still gets the outcome
    rather than an empty stream that hangs."""
    project, source = _project_with_source(tmp_path, "Progress Stream Project")
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())
    job_id = submitted["job"]["id"]

    with client.stream("GET", f"/jobs/{job_id}/progress") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [line for line in response.iter_lines() if line.startswith("data: ")]

    assert len(events) == 1
    payload = json.loads(events[0][len("data: ") :])
    assert payload["status"] == "succeeded"
    assert payload["progress"] == 1.0


def test_a_second_run_on_the_same_source_is_rejected_while_one_is_queued(tmp_path):
    """The guard used to key off a 'running' run. A job that is submitted
    but not yet started is 'pending', and double-submitting would have
    slipped straight past it."""
    project, source = _project_with_source(tmp_path, "Double Submit Project")

    with deferred_jobs():
        first = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        )
        second = client.post(
            f"/projects/{project['id']}/sources/{source['id']}/process",
            json={"sampling_config": {"target_fps": 5.0}},
        )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["code"] == "processing_already_running"


def test_a_finished_run_records_when_it_completed(tmp_path):
    """The inline endpoint used to stamp completed_at; moving the work into
    a worker dropped it on the floor, leaving every detect run with a null
    completion time."""
    project, source = _project_with_source(tmp_path, "Completed At Project")
    submitted = process_source_sync(client, project["id"], source["id"], StubDetector())

    run = client.get(f"/processing-runs/{submitted['run_id']}").json()
    assert run["status"] == "completed"
    assert run["completed_at"] is not None


def test_a_failed_run_also_records_when_it_stopped(tmp_path):
    """A run that dies with a null completed_at looks like it is still
    going, forever."""

    class _ExplodingDetector:
        model_version = "exploding-v1"
        class_names = {0: "car"}

        def detect(self, frame):
            raise RuntimeError("detector exploded")

    project, source = _project_with_source(tmp_path, "Failed Run Project")
    submitted = process_source_sync(client, project["id"], source["id"], _ExplodingDetector())

    run = client.get(f"/processing-runs/{submitted['run_id']}").json()
    assert run["status"] == "failed"
    assert run["completed_at"] is not None
    assert "detector exploded" in run["error_message"]

    job = client.get(f"/jobs/{submitted['job']['id']}").json()
    assert job["status"] == "failed"
