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
    """How far through the frames the labelling has got.

    ``rejected`` is what a human looked at and set aside; ``skipped``
    is what frame selection never offered. Counted apart because they
    say different things about the queue."""

    pending: int
    labeled: int
    rejected: int
    skipped: int
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


class DeletedFrameRead(BaseModel):
    """What went with a deleted frame.

    Reported so the canvas can say what it removed rather than the
    frame simply vanishing.
    """

    labels: int
    detections: int
    #: Tracks that had no detections left once this frame's were gone.
    emptied_tracks: int
    #: Whether a decoded image was on disk and is now not. False when
    #: the frame had never been opened, which costs nothing to delete.
    image_removed: bool


class SourceQueueRead(BaseModel):
    """One source's own place in the labelling queue.

    What the source picker shows: which clip it is, and how much of it
    is still waiting.
    """

    source_id: str
    path_or_uri: str
    type: str
    name: str | None = None
    total: int
    pending: int
    labeled: int
    rejected: int
    skipped: int


class SweepRead(BaseModel):
    """What deleting the unlabelled frames would take, or took."""

    kept: int
    deleted: int
    #: Held back because a dataset version already names them.
    held: int
    bytes_freed: int


class SuggestedBoxRead(BaseModel):
    """A box the model found, offered to the canvas to start from.

    Not an annotation: nothing is saved until the user saves, which
    is what makes correcting one the same gesture as accepting it.
    """

    frame_candidate_id: str
    bbox_json: list[float]
    detector_class: str
    detector_confidence: float
