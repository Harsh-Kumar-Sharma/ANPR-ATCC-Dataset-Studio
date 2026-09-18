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


class FrameAnnotationWrite(BaseModel):
    """One box as the canvas sends it. Geometry is checked against the
    frame server-side; this only rejects the structurally impossible."""

    class_id: int | None = None
    bbox_json: list[float] = Field(min_length=4, max_length=4)
    attributes: dict = Field(default_factory=dict)


class FrameAnnotationsReplace(BaseModel):
    """The complete set of boxes on a frame. Whatever is not in this list
    is gone after the save - including an empty list, which is the label
    "nothing here"."""

    annotations: list[FrameAnnotationWrite]
