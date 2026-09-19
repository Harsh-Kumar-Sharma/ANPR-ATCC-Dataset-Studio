"""move human plate text onto annotations

Ticket 15. A human's plate reading used to be written into
``ocr_candidates`` as a ``source='human'`` row. That table is the
model's record, nothing exporting a dataset ever read it, and the OCR
agreement metric counted those rows as evidence about the model when
they were evidence about the reviewer.

This moves any that exist onto the annotation for the same track, into
``attributes['plate_text']`` - which is where a plate typed on the
labelling canvas already goes - and then deletes them, so there is one
home rather than two.

A human row whose track has no human annotation has nowhere to go. It is
left in place rather than deleted: losing someone's typing to a
migration would be worse than leaving a row behind, and the code no
longer writes or reads one, so it is inert. The upgrade prints how many
it skipped.

Revision ID: 7c41d0a9e5b2
Revises: 2346228ab587
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "7c41d0a9e5b2"
down_revision = "2346228ab587"
branch_labels = None
depends_on = None


#: Same canonical form ``normalize_plate_text`` produces. Inlined rather
#: than imported: a migration has to keep working when the application
#: code around it has moved on.
def _normalize(text: str) -> str:
    return "".join(character for character in text.upper() if character.isalnum())


def upgrade() -> None:
    connection = op.get_bind()

    human_rows = connection.execute(
        sa.text(
            """
            SELECT o.id, o.track_id, o.text, o.normalized_text
            FROM ocr_candidates o
            WHERE o.source = 'human'
            """
        )
    ).fetchall()
    if not human_rows:
        return

    moved, orphaned = [], 0
    for row_id, track_id, text, normalized_text in human_rows:
        annotation = connection.execute(
            sa.text(
                """
                SELECT a.id, a.attributes
                FROM annotations a
                JOIN frame_candidates fc ON a.frame_candidate_id = fc.id
                WHERE fc.track_id = :track_id AND a.source = 'human'
                LIMIT 1
                """
            ),
            {"track_id": track_id},
        ).fetchone()
        if annotation is None:
            orphaned += 1
            continue

        annotation_id, attributes_json = annotation
        attributes = json.loads(attributes_json) if attributes_json else {}
        # Whatever is already on the annotation wins. A plate typed on
        # the canvas is the newer of the two by construction, and this
        # migration is not the place to decide a conflict it cannot see.
        attributes.setdefault("plate_text", normalized_text or _normalize(text or ""))
        if not attributes["plate_text"]:
            attributes.pop("plate_text")

        connection.execute(
            sa.text("UPDATE annotations SET attributes = :attributes WHERE id = :id"),
            {"attributes": json.dumps(attributes), "id": annotation_id},
        )
        moved.append(row_id)

    for row_id in moved:
        connection.execute(sa.text("DELETE FROM ocr_candidates WHERE id = :id"), {"id": row_id})

    print(f"moved {len(moved)} human plate reading(s) onto annotations; left {orphaned} with no label to attach to")


def downgrade() -> None:
    """Deliberately does nothing.

    The plate text is on the annotation, where a plate typed on the
    labelling canvas also lives, and the two are indistinguishable once
    stored. Recreating OCR rows from them would invent human rows for
    readings that were never in that table, which is worse than the
    asymmetry.
    """
