"""What else is true about a labelled object, beyond its class.

An annotation's ``attributes`` is one generic JSON field, so a new
attribute never needs a migration. What it does need is agreement about
what the keys mean, and that lives here: one list, served to the canvas
so it renders the right control, and checked on the way in so the canvas
cannot offer a value the save will refuse.

The alternative - a table of attribute definitions - buys editability
nobody asked for and costs a migration every time the shape changes.
These are properties of the domain (a vehicle has a plate, a colour, a
direction) rather than of a project, unlike classes, which are per
project precisely because two projects really do count different things.

Adding one is a line in ``ATTRIBUTE_DEFINITIONS``. Removing one leaves
whatever was already written in the database alone: old values stay
readable, they just stop being offered, and a box carrying a retired
attribute cannot be re-saved with it. That is deliberate - a value the
UI can no longer show is a value nobody is maintaining.
"""

from typing import Any

#: Longest plate text worth accepting. An Indian plate is thirteen
#: characters with the spaces ("MH 12 AB 1234"); the rest of the room is
#: for international formats and for however a reviewer chooses to space
#: it. It is a guard against a paste accident, not a format check -
#: refusing an unusual plate would be worse than storing one.
PLATE_TEXT_MAX_LENGTH = 32


def _choice(key: str, label: str, options: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "type": "choice",
        "options": [{"value": value, "label": option_label} for value, option_label in options],
    }


#: Every attribute a box can carry, in the order the panel shows them.
ATTRIBUTE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "key": "plate_text",
        "label": "Plate text",
        "type": "text",
        "max_length": PLATE_TEXT_MAX_LENGTH,
        "placeholder": "e.g. MH 12 AB 1234",
    },
    _choice(
        "colour",
        "Colour",
        [
            ("white", "White"),
            ("silver", "Silver"),
            ("grey", "Grey"),
            ("black", "Black"),
            ("red", "Red"),
            ("blue", "Blue"),
            ("green", "Green"),
            ("yellow", "Yellow"),
            ("orange", "Orange"),
            ("brown", "Brown"),
            ("other", "Other"),
        ],
    ),
    _choice(
        "direction",
        "Direction",
        [
            ("incoming", "Incoming"),
            ("outgoing", "Outgoing"),
            ("left_to_right", "Left to right"),
            ("right_to_left", "Right to left"),
        ],
    ),
    {"key": "occluded", "label": "Occluded", "type": "boolean"},
    # Lighting is really a property of the frame, not of one vehicle in
    # it, and recording it per box means two boxes on one frame can
    # disagree. It sits here anyway because the box is the only thing
    # with a generic attributes field, and giving frames one is a
    # migration this ticket exists to avoid. Worth moving the day frames
    # grow attributes of their own.
    {"key": "night", "label": "Night shot", "type": "boolean"},
]

_BY_KEY = {definition["key"]: definition for definition in ATTRIBUTE_DEFINITIONS}


class InvalidAttributeError(ValueError):
    """An attribute that is not one of ours, or a value that is not one
    the attribute can take. A plain ``ValueError`` subclass, so the
    caller wraps it in whatever error its own API speaks rather than
    this module deciding what an HTTP status should be."""


def clean_attributes(attributes: dict | None) -> dict[str, Any]:
    """Check an incoming attribute set and return what should be stored.

    Cleaning, not just checking, because "not set" has to have exactly
    one representation. An empty string and a missing key mean the same
    thing to the person who typed them, and if both can reach the
    database then every later reader has to remember to test for both.
    Clearing a field therefore removes the key.

    Unknown keys are refused rather than passed through. The field is
    schema-free so that *adding* an attribute is cheap, not so that
    anything at all can be written into it - a silently accepted
    ``plate_txt`` is a value nothing will ever read again.
    """
    if attributes is None:
        return {}
    if not isinstance(attributes, dict):
        raise InvalidAttributeError("attributes must be an object.")

    cleaned: dict[str, Any] = {}
    for key, value in attributes.items():
        definition = _BY_KEY.get(key)
        if definition is None:
            known = ", ".join(sorted(_BY_KEY))
            raise InvalidAttributeError(f"unknown attribute {key!r}. Known attributes: {known}.")

        if value is None:
            continue

        kind = definition["type"]
        if kind == "boolean":
            # Not a truthiness test: JSON "true" and 1 are both truthy
            # and neither is a boolean, and storing one makes every
            # later read of this field quietly wrong.
            if not isinstance(value, bool):
                raise InvalidAttributeError(f"attribute {key!r} must be true or false, got {value!r}.")
            cleaned[key] = value
            continue

        if not isinstance(value, str):
            raise InvalidAttributeError(f"attribute {key!r} must be text, got {value!r}.")

        if kind == "choice":
            if value == "":
                continue
            allowed = [option["value"] for option in definition["options"]]
            if value not in allowed:
                raise InvalidAttributeError(f"attribute {key!r} cannot be {value!r}. Expected one of: {', '.join(allowed)}.")
            cleaned[key] = value
            continue

        text = value.strip()
        if not text:
            continue
        max_length = definition.get("max_length")
        if max_length is not None and len(text) > max_length:
            raise InvalidAttributeError(f"attribute {key!r} is {len(text)} characters; the most allowed is {max_length}.")
        cleaned[key] = text

    return cleaned
