"""Which project a model belongs to, for models that predate the idea.

Models were a flat directory shared by every project, so eleven of
them showed up in all three menus with nothing saying which was
which. Ownership is recorded on the sidecar now - but the models
already on disk have none.

Training runs know: each one records the project it ran for and the
model it produced. This reads that back onto the sidecars, once.
"""

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.training_run import TrainingRun
from app.ml.models import list_models, write_sidecar

logger = logging.getLogger(__name__)


def backfill(db: Session, weights_dir: Path) -> list[str]:
    """Give ownerless models the project their training run names.

    Only models that have no owner yet: one already recorded is not
    second-guessed. A model whose training run has since been cleared
    off the list stays ownerless, because nothing knows any more -
    and ownerless means "shown everywhere", which is the safe way to
    be ignorant.
    """
    ownerless = {
        model.id for model in list_models(weights_dir) if model.kind == "custom" and model.project_id is None
    }
    if not ownerless:
        return []

    claimed: list[str] = []
    for run in db.scalars(select(TrainingRun).where(TrainingRun.output_model_id.is_not(None))):
        if run.output_model_id in ownerless:
            write_sidecar(weights_dir, run.output_model_id, project_id=run.project_id)
            ownerless.discard(run.output_model_id)
            claimed.append(run.output_model_id)

    if claimed:
        logger.info("Recorded the owning project for %d model(s): %s", len(claimed), ", ".join(claimed))
    return claimed
