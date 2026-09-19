from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.db.session import get_db
from app.schemas.active_learning import DisagreementItemRead, QueueItemRead
from app.services.active_learning import (
    build_hard_failed_queue,
    build_low_confidence_queue,
    find_model_human_disagreements,
)

router = APIRouter(prefix="/projects/{project_id}/active-learning", tags=["active-learning"])


@router.get("/low-confidence-queue", response_model=list[QueueItemRead])
def get_low_confidence_queue(project_id: str, limit: int = 50, db: Session = Depends(get_db)) -> list[QueueItemRead]:
    """Unreviewed tracks, least-confident first (docs/02_IMPLEMENTATION_PLAN.md
    Phase 8: uncertainty-sampling triage for reviewers)."""
    get_project_or_404(db, project_id)
    items = build_low_confidence_queue(db, project_id, limit=limit)
    return [QueueItemRead(**asdict(item)) for item in items]


@router.get("/hard-failed-queue", response_model=list[QueueItemRead])
def get_hard_failed_queue(project_id: str, limit: int = 50, db: Session = Depends(get_db)) -> list[QueueItemRead]:
    """Every track flagged difficult by the pipeline or a human -
    the reason HARD/FAILED evidence has never been deleted since
    Phase 2."""
    get_project_or_404(db, project_id)
    items = build_hard_failed_queue(db, project_id, limit=limit)
    return [QueueItemRead(**asdict(item)) for item in items]


@router.get("/disagreements", response_model=list[DisagreementItemRead])
def get_model_human_disagreements(project_id: str, db: Session = Depends(get_db)) -> list[DisagreementItemRead]:
    """Labels worth a second look: a human class the detector's own class
    does not allow for, or a vehicle the human drew and the detector
    never found.

    NOT true model-ensemble disagreement - only one trained model exists.
    See docs/HANDOFF.md Phase 8 for why. Covers labels written on the
    canvas as well as through track review; a canvas box is matched to a
    detection by overlap, and reported as a miss when nothing matches.
    """
    get_project_or_404(db, project_id)
    items = find_model_human_disagreements(db, project_id)
    return [DisagreementItemRead(**asdict(item)) for item in items]
