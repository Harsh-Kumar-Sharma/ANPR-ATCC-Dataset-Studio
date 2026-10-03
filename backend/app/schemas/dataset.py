from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DatasetExportRequest(BaseModel):
    train_ratio: float = Field(default=0.75, gt=0, lt=1)
    val_ratio: float = Field(default=0.15, ge=0, lt=1)
    test_ratio: float = Field(default=0.1, ge=0, lt=1)
    split_seed: int | None = None

    @model_validator(mode="after")
    def _ratios_sum_to_one(self) -> "DatasetExportRequest":
        total = self.train_ratio + self.val_ratio + self.test_ratio
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"train_ratio + val_ratio + test_ratio must sum to 1.0, got {total}")
        return self


class DatasetVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    version: int
    created_at: datetime
    split_seed: int
    config_snapshot_json: dict
    #: How large this version is on disk, which is roughly how large
    #: its download will be. Shown so a click is an informed one.
    bytes_on_disk: int = 0


class ValidationResultRead(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str] = []


class DatasetExportResult(BaseModel):
    """What an export produced, as the manifest recorded it.

    ``counts`` is images and ``object_counts`` is boxes, and they are
    different numbers now that a frame can hold many boxes - conflating
    them is how "50 labelled frames" came back as 75.
    """

    dataset_version: DatasetVersionRead
    counts: dict[str, int]
    object_counts: dict[str, int]
    #: Images exported with an empty label file because a human labelled
    #: the frame as holding nothing. Deliberate negative examples.
    background_frames: int = 0
    #: Exported frames carrying a box with no class; those boxes are not
    #: in the label file.
    frames_with_unclassified_boxes: int = 0
    #: Labelled frames left out of the export entirely, because not one
    #: of their boxes had a class. Reported so an export that shrank can
    #: say by how much.
    frames_skipped_unclassified: int = 0
    #: Labelled frames left out because their image is gone - a live
    #: capture that was never recorded, or an imported video that has
    #: since moved. Reported so the work that did not land is visible.
    frames_skipped_unrecoverable: int = 0
    validation: ValidationResultRead
