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
