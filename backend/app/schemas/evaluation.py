from pydantic import BaseModel, Field


class FreezeSourceRequest(BaseModel):
    ground_truth_vehicle_count: int = Field(gt=0)


class TrackCounts(BaseModel):
    total: int
    confirmed: int
    failed: int
    unreviewed: int


class OcrMetrics(BaseModel):
    tracks_with_ocr: int
    agreements: int
    corrections: int
    agreement_rate: float | None


class FailureGalleryItem(BaseModel):
    track_id: str
    bucket: str | None
    review_status: str
    representative_frame_id: str | None


class EvaluationReport(BaseModel):
    run_id: str
    source_id: str
    is_frozen_validation_clip: bool
    ground_truth_vehicle_count: int | None
    track_counts: TrackCounts
    detection_recall: float | None
    duplicate_track_pairs: list[tuple[str, str]]
    fragmented_track_pairs: list[tuple[str, str]]
    class_distribution: dict[str, int]
    ocr_metrics: OcrMetrics
    failure_gallery: list[FailureGalleryItem]
