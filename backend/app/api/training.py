"""Training a model without leaving the app.

The app used to write a `data.yaml` and a `RETRAINING.md` and stop,
leaving the user to copy a command into a terminal. These endpoints
run it on the job machinery that already carries long work.
"""

from dataclasses import asdict  # noqa: F401  (kept for symmetry with sibling routers)

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.models.dataset_version import DatasetVersion
from app.db.models.training_run import TrainingRun
from app.db.session import get_db
from app.schemas.training import ForgottenRuns, TrainingRunRead, TrainingStarted, TrainingStartRequest
from app.services import training
from app.services.jobs.runner import Launcher, get_launcher, submit_job

router = APIRouter(prefix="/projects/{project_id}/training-runs", tags=["training"])
runs_router = APIRouter(prefix="/training-runs", tags=["training"])


@router.post("", response_model=TrainingStarted, status_code=202)
def start_training(
    project_id: str,
    payload: TrainingStartRequest,
    db: Session = Depends(get_db),
    launcher: Launcher = Depends(get_launcher),
) -> TrainingStarted:
    """Start training, and return as soon as it is queued.

    Everything that could be wrong with the request is checked here
    rather than in the worker: finding out an hour later, through a
    failed job, that a dataset was never exported is a poor way to
    learn it.
    """
    project = get_project_or_404(db, project_id)

    version = db.get(DatasetVersion, payload.dataset_version_id)
    if version is None or version.project_id != project.id:
        raise NotFoundError(f"Dataset version not found in this project: {payload.dataset_version_id}")

    run = training.prepare(
        db,
        project=project,
        dataset_version=version,
        base_model_id=payload.base_model_id,
        weights_dir=get_settings().resolved_model_weights_dir(),
        epochs=payload.epochs,
        image_size=payload.image_size,
    )
    # Committed before the job is launched, not after. The worker is
    # a separate process with its own connection: a row that is only
    # flushed does not exist as far as it is concerned, and the job
    # would fail with "training run not found". Detection commits
    # here for the same reason.
    db.commit()
    db.refresh(run)

    job = submit_job(
        db,
        type="train",
        project_id=project.id,
        params={"training_run_id": run.id},
        launcher=launcher,
    )
    run.job_id = job.id
    db.commit()
    db.refresh(run)

    return TrainingStarted(run=TrainingRunRead.model_validate(run), job_id=job.id)


@router.get("", response_model=list[TrainingRunRead])
def list_training_runs(project_id: str, db: Session = Depends(get_db)) -> list[TrainingRunRead]:
    """This project's training runs, most recent first."""
    get_project_or_404(db, project_id)
    runs = db.scalars(
        select(TrainingRun)
        .where(TrainingRun.project_id == project_id)
        .order_by(TrainingRun.started_at.desc())
    )
    return [TrainingRunRead.model_validate(r) for r in runs]


@runs_router.get("/{run_id}", response_model=TrainingRunRead)
def get_training_run(run_id: str, db: Session = Depends(get_db)) -> TrainingRunRead:
    return TrainingRunRead.model_validate(training.get_run(db, run_id))


@router.delete("", response_model=ForgottenRuns)
def forget_finished_runs(project_id: str, db: Session = Depends(get_db)) -> ForgottenRuns:
    """Clear this project's finished training runs off the list.

    Rows only. Models they trained stay in the models directory, and
    their checkpoints stay on disk - clearing an old failure off a
    screen should not delete a model.
    """
    get_project_or_404(db, project_id)
    forgotten = training.forget_finished(db, project_id)
    db.commit()
    return ForgottenRuns(forgotten=forgotten)


@runs_router.delete("/{run_id}", status_code=204)
def forget_training_run(run_id: str, db: Session = Depends(get_db)) -> None:
    """Take one finished run off the list. Its model stays."""
    training.forget(db, training.get_run(db, run_id))
    db.commit()
