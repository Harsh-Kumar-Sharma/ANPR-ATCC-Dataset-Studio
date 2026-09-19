from typing import Literal

from pydantic import BaseModel


class QueueItemRead(BaseModel):
    track_id: str
    review_status: str
    bucket: str | None
    confidence: float
    representative_frame_id: str | None


class DisagreementItemRead(BaseModel):
    """One label worth a second look, and why.

    ``class_mismatch`` means the detector saw something there and the
    human's class does not fit it. ``missed_detection`` means the human
    drew a vehicle the detector never found - there is no detector class
    and no track, which is the whole point of the entry.
    """

    kind: Literal["class_mismatch", "missed_detection"]
    frame_id: str
    annotation_id: str
    human_class_id: int
    human_class_name: str
    #: Null for a missed detection.
    detector_class: str | None = None
    #: Null for a box drawn on the canvas - it belongs to a frame and to
    #: no track, so there is nowhere for a track-review button to go.
    track_id: str | None = None


class ClassCountRead(BaseModel):
    class_id: int
    name: str
    box_count: int


class LabelBalanceRead(BaseModel):
    """What a labeller has produced across a whole project.

    A different question from the evaluation report's class
    distribution, which is scoped to one processing run and so cannot
    account for a box drawn on the canvas at all.
    """

    classes: list[ClassCountRead]
    total_boxes: int
    unclassified_boxes: int
    labeled_frames: int
    background_frames: int


class RetrainingHandoffResult(BaseModel):
    data_yaml_path: str
    instructions_path: str
    data_yaml_content: str
    instructions_content: str
