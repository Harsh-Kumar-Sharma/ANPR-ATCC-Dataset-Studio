from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models.processing_run import ProcessingRun
from app.db.models.project import Project
from app.db.models.source import Source
from app.db.session import get_db
from app.schemas.evaluation import EvaluationReport
from app.services.evaluation import evaluate_run

router = APIRouter(prefix="/processing-runs", tags=["evaluation"])


@router.get("/{run_id}/evaluation", response_model=EvaluationReport)
def get_run_evaluation(run_id: str, db: Session = Depends(get_db)) -> dict:
    """docs/02_IMPLEMENTATION_PLAN.md Phase 7: everything measurable
    about one processing run - detection recall (only if the source is
    a frozen validation clip with a ground-truth count), duplicate/
    fragmentation heuristics, ATCC class distribution, OCR agreement,
    and a failure gallery."""
    run = db.get(ProcessingRun, run_id)
    if run is None:
        raise NotFoundError(f"Processing run not found: {run_id}")

    source = db.get(Source, run.source_id)
    if source is None:
        raise NotFoundError(f"Source not found for run: {run_id}")

    project = db.get(Project, source.project_id)
    if project is None:
        raise NotFoundError(f"Project not found for run: {run_id}")

    return evaluate_run(db, run, source, project)
