from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    #: Which class list to copy in. Defaults to ATCC, what every project
    #: had before classes became per-project.
    class_preset: str | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime
    #: The preset this project was seeded from. A record of where its
    #: classes came from, NOT the source of truth for them - that is
    #: the project's own class rows, which the user may have edited.
    class_schema_version: str
    workspace_path: str


class ProjectDeleteRequest(BaseModel):
    """Naming the project is the interlock on deleting it.

    Required, and compared exactly. A mis-aimed request - the wrong id
    in a URL, a page left open while something else changed - should not
    be able to destroy a project the user was not looking at.
    """

    name: str


class ProjectContentsRead(BaseModel):
    """What a project holds, in the terms a person would miss it in.

    Shown before deleting and returned after, so what the confirmation
    promised and what happened can be compared.
    """

    sources: int
    frames: int
    tracks: int
    #: Human boxes. Predictions are not counted - nobody mourns one, and
    #: counting them would inflate the number the confirmation leans on.
    labels: int
    dataset_versions: int
    #: Usually the bulk of what is destroyed, and the only part measured
    #: in gigabytes.
    workspace_bytes: int
    #: Any at all and deletion refuses.
    running_jobs: int
    #: False when there was no directory, or when it was somewhere the
    #: server refuses to delete from.
    workspace_removed: bool = False
