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
