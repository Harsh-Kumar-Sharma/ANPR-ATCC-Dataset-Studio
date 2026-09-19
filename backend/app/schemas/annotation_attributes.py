from typing import Literal

from pydantic import BaseModel


class AttributeOptionRead(BaseModel):
    """One value a ``choice`` attribute can take.

    The value is what gets stored and the label is what a human reads;
    they differ because ``left_to_right`` is a sane thing to query on and
    a poor thing to put in a dropdown.
    """

    value: str
    label: str


class AttributeDefinitionRead(BaseModel):
    """One attribute a box can carry, and how to edit it.

    ``type`` decides the control: a text field, a dropdown, or a
    checkbox. The canvas renders from this rather than from its own copy
    of the list, so the two cannot drift into a UI that offers values the
    server rejects.
    """

    key: str
    label: str
    type: Literal["text", "choice", "boolean"]
    #: Present for ``choice`` only.
    options: list[AttributeOptionRead] | None = None
    #: Present for ``text`` only.
    max_length: int | None = None
    placeholder: str | None = None
