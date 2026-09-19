"""What a labeller has produced, across a whole project.

Separate from the evaluation report on purpose. That one is scoped to a
processing run, which is right for asking how a detection pass went and
wrong for asking what a person has labelled - a box drawn on the canvas
belongs to a frame, and the frame's source may have several runs.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.projects import get_project_or_404
from app.db.session import get_db
from app.schemas.active_learning import LabelBalanceRead
from app.services.label_summary import label_balance

router = APIRouter(prefix="/projects/{project_id}/label-balance", tags=["labels"])


@router.get("", response_model=LabelBalanceRead)
def get_label_balance(project_id: str, db: Session = Depends(get_db)) -> LabelBalanceRead:
    """This project's human labels counted by class, whichever way they
    were written - track review or the labelling canvas."""
    get_project_or_404(db, project_id)
    return LabelBalanceRead(**asdict(label_balance(db, project_id)))
