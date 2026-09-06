from pydantic import BaseModel


class QueueItemRead(BaseModel):
    track_id: str
    review_status: str
    bucket: str | None
    confidence: float
    representative_frame_id: str | None


class DisagreementItemRead(BaseModel):
    track_id: str
    annotation_id: str
    detector_class: str
    human_class_id: int
    human_class_name: str


class RetrainingHandoffResult(BaseModel):
    data_yaml_path: str
    instructions_path: str
    data_yaml_content: str
    instructions_content: str
