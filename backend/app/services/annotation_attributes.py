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

from app.core.errors import AppError
from app.services.text_normalization import normalize_plate_text

#: Longest plate text worth accepting, measured on the canonical form -
#: uppercase, letters and digits only, the same shape the OCR path
#: stores. An Indian plate is ten characters that way; the rest of the
#: room is for international formats. It is a guard against a paste
#: accident, not a format check - refusing an unusual plate would be
#: worse than storing one. Measuring the canonical form matters: a limit
#: on raw typing would be a limit on how someone spaces a plate.
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
        "placeholder": "e.g. MH12AB1234",
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


class InvalidAttributeError(AppError):
    """An attribute that is not one of ours, or a value it cannot take.

    An ``AppError`` like every other service error here, so a caller that
    forgets to wrap it still produces a 400 the user can act on rather
    than reaching the catch-all and becoming an opaque 500.
    """

    code = "invalid_attribute"


def clean_attributes(attributes: dict | None, stored: dict | None = None) -> dict[str, Any]:
    """Check an incoming attribute set and return what should be stored.

    Cleaning, not just checking, because "not set" has to have exactly
    one representation. An empty string and a missing key mean the same
    thing to the person who typed them, and if both can reach the
    database then every later reader has to remember to test for both.
    Clearing a field therefore removes the key - and so does ``False``,
    because a checkbox has two states and cannot produce an explicit
    false, so storing one would give "not occluded" a second spelling
    that nothing on screen could ever have written.

    Unknown keys are refused. The field is schema-free so that *adding*
    an attribute is cheap, not so that anything at all can be written
    into it - a silently accepted ``plate_txt`` is a value nothing will
    ever read again.

    ``stored`` is what the annotation already holds, and it is the one
    thing that excuses an unrecognised key or value. The canvas loads a
    box and sends its whole attribute set back; if an attribute has been
    retired since, that set carries something the panel cannot render and
    the user cannot clear. Refusing it would make the entire frame
    unsavable over a value that is invisible, so an *unchanged echo* of
    something already stored is dropped instead. Anything else - a key
    that is not stored, or a stored key with a new value - is still a
    client writing something nothing will read, and is refused.
    """
    if attributes is None:
        return {}
    if not isinstance(attributes, dict):
        raise InvalidAttributeError("attributes must be an object.")

    stored = stored or {}
    cleaned: dict[str, Any] = {}
    for key, value in attributes.items():
        definition = _BY_KEY.get(key)
        if definition is None:
            if _is_echo(key, value, stored):
                continue
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
            if value:
                cleaned[key] = True
            continue

        if not isinstance(value, str):
            raise InvalidAttributeError(f"attribute {key!r} must be text, got {value!r}.")

        if kind == "choice":
            if value == "":
                continue
            allowed = [option["value"] for option in definition.get("options", [])]
            if value not in allowed:
                if _is_echo(key, value, stored):
                    continue
                raise InvalidAttributeError(f"attribute {key!r} cannot be {value!r}. Expected one of: {', '.join(allowed)}.")
            cleaned[key] = value
            continue

        text = _canonicalise(definition, value)
        if not text:
            continue
        max_length = definition.get("max_length")
        if max_length is not None and len(text) > max_length:
            # Echoed values are excused here too, not only for unknown
            # keys and retired options. A value already in the database
            # that is too long for today's limit - written before the
            # limit existed, or by a migration - would otherwise make
            # every save of its frame fail over something the panel
            # cannot shorten because it never showed it.
            if _is_echo(key, value, stored):
                continue
            raise InvalidAttributeError(f"attribute {key!r} is {len(text)} characters; the most allowed is {max_length}.")
        cleaned[key] = text

    return cleaned


def _is_echo(key: str, value: Any, stored: dict) -> bool:
    """Is this exactly what the annotation already holds?

    The test for "the canvas handed back what it was given" rather than
    "a client made something up". Only an unchanged echo is droppable -
    anything else is a value somebody meant.
    """
    return key in stored and stored[key] == value


def _canonicalise(definition: dict, value: str) -> str:
    """Bring a text value into the form the rest of the app stores.

    Plate text goes through the same ``normalize_plate_text`` the OCR
    path uses. Without that, the plate a human types on the canvas and
    the plate the model read of the same vehicle could never compare
    equal - which would make recording it here pointless, since the
    whole reason to keep plate text in one place is to be able to
    compare the two.
    """
    if definition["key"] == "plate_text":
        return normalize_plate_text(value)
    return value.strip()
