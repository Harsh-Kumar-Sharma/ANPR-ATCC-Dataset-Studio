from pydantic import BaseModel, ConfigDict, Field


class ClassDefinitionRead(BaseModel):
    """A class as the editor sees it.

    ``class_id`` is what labels point at; ``id`` is the row. They are
    different on purpose - see the model.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    class_id: int
    name: str
    display_order: int


class ClassDefinitionCreate(BaseModel):
    #: Trimmed and checked for a case-insensitive clash server-side; the
    #: length cap here only stops something absurd reaching the column.
    name: str = Field(min_length=1, max_length=128)


class ClassDefinitionRename(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ClassUsage(BaseModel):
    """How many labels a class is holding.

    The editor asks before offering a delete, so it can say what would be
    affected rather than failing the attempt.
    """

    class_id: int
    label_count: int


class ClassDeleteOutcome(BaseModel):
    """What deleting a class did to the labels that were using it."""

    class_id: int
    remapped: int
    deleted_labels: int
