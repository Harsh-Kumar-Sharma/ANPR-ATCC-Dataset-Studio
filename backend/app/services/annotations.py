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
    """
    ids = list(annotation_ids)
    if not ids:
        return 0

    # Resolved before the labels go, while the join still holds.
    track_ids: set[str] = set()
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

    db.flush()
    return len(ids)
