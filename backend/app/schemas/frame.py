from pydantic import BaseModel, ConfigDict, Field


class FrameRead(BaseModel):
    """A frame as the queue and the canvas see it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    frame_index: int
    timestamp_ms: int
    width: int
    height: int
    status: str
    selection_reason: str | None


class FrameStatusWrite(BaseModel):
    """Move a frame to a place in the queue by hand - normally to
    ``rejected`` to skip it, or back to ``pending`` to undo that."""

    status: str


class QueueProgress(BaseModel):
    """How far through the frames the labelling has got."""

    pending: int
    labeled: int
    rejected: int
    total: int


class FrameAnnotationWrite(BaseModel):
    """One box as the canvas sends it. Geometry is checked against the
    frame server-side; this only rejects the structurally impossible."""

    #: The annotation this box already is, when the canvas loaded it
    #: rather than drew it. An echoed id is updated in place, so an
    #: unchanged box keeps its identity and everything that refers to
    #: it; a box without one is new.
    id: str | None = None
    class_id: int | None = None
    bbox_json: list[float] = Field(min_length=4, max_length=4)
    attributes: dict = Field(default_factory=dict)


class FrameAnnotationsReplace(BaseModel):
    """The complete set of boxes on a frame. Whatever is not in this list
    is gone after the save - including an empty list, which is the label
    "nothing here"."""

    annotations: list[FrameAnnotationWrite]
