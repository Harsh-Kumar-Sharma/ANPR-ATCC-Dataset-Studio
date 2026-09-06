import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class DatasetItem(Base):
    """One exported sample within a dataset version. See docs/05_DATABASE_DESIGN.md.

    ``id`` is additive (not in the documented schema, which only lists
    dataset_version_id/annotation_id/split/export_path) - added for
    consistency with every other table in this codebase and so a
    single item can be addressed directly.
    """

    __tablename__ = "dataset_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False, index=True)
    annotation_id: Mapped[str] = mapped_column(ForeignKey("annotations.id"), nullable=False)
    split: Mapped[str] = mapped_column(String(16), nullable=False)
    export_path: Mapped[str] = mapped_column(String(1024), nullable=False)
