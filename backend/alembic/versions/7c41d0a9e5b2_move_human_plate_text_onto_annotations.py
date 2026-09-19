"""move human plate text onto annotations

Ticket 15. A human's plate reading used to be written into
``ocr_candidates`` as a ``source='human'`` row. That table is the
model's record, nothing exporting a dataset ever read it, and the OCR
agreement metric counted those rows as evidence about the model when
they were evidence about the reviewer.

This moves them onto the annotation for the same track, into
``attributes['plate_text']`` - which is where a plate typed on the
labelling canvas already goes - so there is one home rather than two.

**One rule decides what is deleted: a row goes only when its own reading
is already accounted for.** That means the reading is empty (it says
nothing), or it equals the plate the annotation ends up carrying. Every
other row stays exactly where it is, whatever the reason - no label to
attach to, a plate the annotation disagrees with, an earlier correction
saying something else, a reading too long for the attribute validator to
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


def _reading(row) -> str:
    """What this row says, in the form the app would store it.

    ``normalized_text`` is canonicalised again rather than trusted: it is
    a column, and a column can have been hand-edited or written by an
    older rule. Re-running the normaliser is free and makes the
    comparison below one between like and like.
    """
    return _normalize(row.normalized_text or row.text)


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

    written = cleared = orphaned = conflicted = too_long = superseded = 0

    for track_id, track_rows in by_track.items():
        # The one the old UI showed as current. ``selected`` is what it
        # displayed; failing that, the most recent.
        current = next((r for r in reversed(track_rows) if r.selected), track_rows[-1])
        reading = _reading(current)

        annotation = connection.execute(
            sa.text(
                """
                SELECT a.id, a.attributes
                FROM annotations a
                JOIN frame_candidates fc ON a.frame_candidate_id = fc.id
                WHERE fc.track_id = :track_id AND a.source = 'human'
                ORDER BY a.updated_at DESC, a.rowid DESC
                LIMIT 1
                """
            ),
            {"track_id": track_id},
        ).fetchone()
        if annotation is None:
            orphaned += len(track_rows)
            continue

        attributes = json.loads(annotation.attributes) if annotation.attributes else {}
        final_plate = attributes.get("plate_text") or ""

        if not final_plate and reading:
            if len(reading) > _PLATE_TEXT_MAX_LENGTH:
                # Writing it would leave a frame the app refuses to save:
                # the attribute validator checks length and this path
                # skips it. Left where it is, and reported.
                too_long += len(track_rows)
                continue
            attributes["plate_text"] = reading
            connection.execute(
                sa.text("UPDATE annotations SET attributes = :attributes WHERE id = :id"),
                {"attributes": json.dumps(attributes), "id": annotation.id},
            )
            final_plate = reading
            written += 1

        # The one rule. A row whose reading is empty says nothing worth
        # keeping; a row whose reading is what the annotation now carries
        # is a duplicate of it. Anything else is a value that exists
        # nowhere else, so it stays - including an earlier correction the
        # current one replaced, and every row on a track whose annotation
        # already disagreed.
        for row in track_rows:
            row_reading = _reading(row)
            if not row_reading or row_reading == final_plate:
                connection.execute(sa.text("DELETE FROM ocr_candidates WHERE id = :id"), {"id": row.id})
                cleared += 1
            elif row is current:
                conflicted += 1
            else:
                superseded += 1

    logger.info(
        "plate text: wrote %d plate(s) onto annotations and removed %d row(s). Left in place: "
        "%d with no label to attach to, %d disagreeing with a plate already recorded, "
        "%d superseded by a later correction saying something else, %d too long to store.",
        written,
        cleared,
        orphaned,
        conflicted,
        superseded,
        too_long,
    )


def downgrade() -> None:
    """Deliberately does nothing.

    The plate text is on the annotation, where a plate typed on the
    labelling canvas also lives, and the two are indistinguishable once
    stored. Recreating OCR rows from them would invent human rows for
    readings that were never in that table, which is worse than the
    asymmetry.
    """
