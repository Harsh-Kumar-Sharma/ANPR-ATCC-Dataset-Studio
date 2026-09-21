from pydantic import BaseModel


class ModelRead(BaseModel):
    """One model the app can be asked to detect with."""

    id: str
    label: str
    #: "builtin" or "custom".
    kind: str
    weights_file: str
    #: False for a built-in whose weights have not been fetched yet.
    #: Still offerable: the first run downloads it.
    present: bool
    bytes: int
    note: str = ""
    #: Which project owns this model. Null for a built-in, and for a
    #: file dropped into the models directory by hand.
    project_id: str | None = None
    #: What a custom model detects, as its checkpoint reports it.
    classes: list[str] = []


class ModelImportRequest(BaseModel):
    """Bring a model you trained into the app.

    A path rather than an upload: the file is already on this machine,
    and pushing a few hundred megabytes through HTTP to land it in a
    directory beside the one it came from helps nobody.
    """

    path: str
    #: What to call it. Defaults to the filename.
    name: str | None = None
    #: The project bringing it in. A model is offered to the project
    #: it belongs to rather than to every project in the app.
    project_id: str | None = None
