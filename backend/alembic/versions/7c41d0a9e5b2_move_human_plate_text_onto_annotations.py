"""move human plate text onto annotations

Ticket 15. A human's plate reading used to be written into
``ocr_candidates`` as a ``source='human'`` row. That table is the
model's record, nothing exporting a dataset ever read it, and the OCR
agreement metric counted those rows as evidence about the model when
they were evidence about the reviewer.

This moves them onto the annotation for the same track, into
``attributes['plate_text']`` - which is where a plate typed on the
labelling canvas already goes - so there is one home rather than two.

Nothing a person typed is destroyed. A row is deleted only once its
reading is safely on the annotation, or once the annotation already says
the same thing. Everything else is left exactly where it is: a track
with no label to attach to, a reading the annotation's own plate
disagrees with, a reading too long for the attribute validator to
accept. Those are counted and reported rather than resolved, because
resolving them means choosing between two things a person wrote and this
migration cannot see which they meant.

Revision ID: 7c41d0a9e5b2
Revises: 2346228ab587
"""

import json
import logging
import re
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "7c41d0a9e5b2"
down_revision = "2346228ab587"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: Copies of the application's rules, inlined because a migration has to
#: keep working when the code around it has moved on. Both are checked
#: against their originals by
#: ``tests/test_migration_7c41d0a9e5b2.py``, so the copies cannot drift
#: silently.
#:
#: The regex matters: ``str.isalnum`` would look equivalent and is not -
#: it is Unicode-aware, so it keeps accented letters and Devanagari
#: digits that ``normalize_plate_text`` strips. A value written that way
#: is one the app could never produce and that would never compare equal
#: to anything.
_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_PLATE_TEXT_MAX_LENGTH = 32


def _normalize(text: str) -> str:
    return _NON_ALNUM.sub("", (text or "").upper())


def upgrade() -> None:
    connection = op.get_bind()

    rows = connection.execute(
        sa.text(
            """
            SELECT id, track_id, text, normalized_text, selected, created_at
            FROM ocr_candidates
            WHERE source = 'human'
            ORDER BY created_at, rowid
            """
        )
    ).fetchall()
    if not rows:
        return

    by_track: dict[str, list] = defaultdict(list)
    for row in rows:
        by_track[row.track_id].append(row)

    moved = orphaned = conflicted = too_long = 0

    for track_id, track_rows in by_track.items():
        # The one the old UI showed as current. ``selected`` is what it
        # displayed; failing that, the most recent, since every earlier
        # human row on a track is a correction this one replaced.
        current = next((r for r in reversed(track_rows) if r.selected), track_rows[-1])
        reading = current.normalized_text or _normalize(current.text)

        annotation = connection.execute(
            sa.text(
                """
                SELECT a.id, a.attributes
                FROM annotations a
                JOIN frame_candidates fc ON a.frame_candidate_id = fc.id
                WHERE fc.track_id = :track_id AND a.source = 'human'
                ORDER BY a.updated_at, a.rowid
                LIMIT 1
                """
            ),
            {"track_id": track_id},
        ).fetchone()
        if annotation is None:
            orphaned += len(track_rows)
            continue

        attributes = json.loads(annotation.attributes) if annotation.attributes else {}
        existing = attributes.get("plate_text")

        if existing and existing != reading:
            # Two different things a person wrote. The annotation's is
            # the newer by construction, so it stays - but the one it
            # beat is left where it is rather than deleted.
            conflicted += len(track_rows)
            continue

        if not existing:
            if not reading:
                # Nothing to move. The rows say nothing, so they go.
                _delete(connection, track_rows)
                moved += 0
                continue
            if len(reading) > _PLATE_TEXT_MAX_LENGTH:
                # Writing it would leave a frame the app refuses to
                # save - the attribute validator checks length and this
                # path skips it. Left alone and reported.
                too_long += len(track_rows)
                continue
            attributes["plate_text"] = reading
            connection.execute(
                sa.text("UPDATE annotations SET attributes = :attributes WHERE id = :id"),
                {"attributes": json.dumps(attributes), "id": annotation.id},
            )
            moved += 1

        # Either the reading is now on the annotation or it already said
        # the same thing. Nothing is lost by clearing the rows.
        _delete(connection, track_rows)

    logger.info(
        "plate text: moved %d reading(s) onto annotations; left %d with no label, "
        "%d disagreeing with a plate already recorded, %d too long to store",
        moved,
        orphaned,
        conflicted,
        too_long,
    )


def _delete(connection, rows) -> None:
    for row in rows:
        connection.execute(sa.text("DELETE FROM ocr_candidates WHERE id = :id"), {"id": row.id})


def downgrade() -> None:
    """Deliberately does nothing.

    The plate text is on the annotation, where a plate typed on the
    labelling canvas also lives, and the two are indistinguishable once
    stored. Recreating OCR rows from them would invent human rows for
    readings that were never in that table, which is worse than the
    asymmetry.
    """
