"""Removing annotations, and everything that must go with them.

An annotation is not a leaf. A dataset version may index it, and if it
was a human review then its track claims to have been reviewed. Delete
the row alone and both of those keep pointing at something that is not
there - silently, because SQLite is not enforcing foreign keys here.

One function owns that cleanup so every caller that removes labels (a
class being deleted, a frame's boxes being replaced) settles the same
things the same way.
"""

from collections.abc import Iterable, Iterator

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models.annotation import Annotation
from app.db.models.dataset_item import DatasetItem
from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.track import Track

#: SQLite caps a statement at 32 766 bound parameters, and ``IN (...)``
#: over a materialised id list spends one per id. Well under that.
STATEMENT_BATCH = 500


def chunked(items: list, size: int = STATEMENT_BATCH) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def delete_annotations(db: Session, annotation_ids: Iterable[str]) -> int:
    """Delete these annotations and settle what depended on them.

    Dataset item rows indexing them go too. The export on disk stays as
    the durable record of what a version contained; the item row was
    only ever an index into it.

    A track whose *human* review is among them goes back to unreviewed,
    because "accepted" would now describe a review that no longer
    exists. Only human: a model prediction is not a review, and removing
    one says nothing about whether the track was looked at - which is
    the same line ``review.py`` draws when it writes review_status.

    A frame left with no human boxes goes back to ``pending`` for the
    same reason: ``labeled`` would be describing work that is no longer
    there. It matters more than it looks - the export reads a
    ``labeled`` frame with no boxes on it as a human saying "nothing
    here", so a frame whose labels were deleted with their class would
    otherwise ship as a background image asserting the vehicles in it
    do not exist.
    """
    ids = list(annotation_ids)
    if not ids:
        return 0

    # Resolved before the labels go, while the join still holds.
    track_ids: set[str] = set()
    frame_ids: set[str] = set()
    for chunk in chunked(ids):
        frame_ids.update(db.scalars(select(Annotation.frame_id).where(Annotation.id.in_(chunk))))
    for chunk in chunked(ids):
        track_ids.update(
            db.scalars(
                select(FrameCandidate.track_id)
                .join(Annotation, Annotation.frame_candidate_id == FrameCandidate.id)
                .where(Annotation.id.in_(chunk), Annotation.source == "human")
            )
        )

    for chunk in chunked(ids):
        db.execute(delete(DatasetItem).where(DatasetItem.annotation_id.in_(chunk)))
        db.execute(delete(Annotation).where(Annotation.id.in_(chunk)))

    for chunk in chunked(sorted(track_ids)):
        db.execute(update(Track).where(Track.id.in_(chunk)).values(review_status="unreviewed"))

    _restate_emptied_frames(db, sorted(frame_ids))

    db.flush()
    return len(ids)


def _restate_emptied_frames(db: Session, frame_ids: list[str]) -> None:
    """Put a frame back to ``pending`` once its last human box is gone.

    Only ``labeled`` frames are touched. ``rejected`` and ``skipped``
    are judgements about whether a frame is worth labelling at all, and
    deleting boxes does not reverse either of them.
    """
    if not frame_ids:
        return

    still_labelled: set[str] = set()
    for chunk in chunked(frame_ids):
        still_labelled.update(
            db.scalars(
                select(Annotation.frame_id).where(Annotation.frame_id.in_(chunk), Annotation.source == "human")
            )
        )

    emptied = [frame_id for frame_id in frame_ids if frame_id not in still_labelled]
    for chunk in chunked(emptied):
        db.execute(
            update(Frame).where(Frame.id.in_(chunk), Frame.status == "labeled").values(status="pending")
        )
