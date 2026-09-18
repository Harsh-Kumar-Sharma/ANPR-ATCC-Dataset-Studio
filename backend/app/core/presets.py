"""Starting points for a project's class list.

A preset is a *template*, never a live schema. Creating a project copies
one into the project's own rows, so editing one project's classes can
never reach across and change another's - which is the whole reason the
old hardcoded module had to go: every project shared one list, and an
ANPR project needs a much shorter one than a traffic survey.

Nothing outside project creation should read these. Once a project
exists, its classes are whatever its own rows say, including any the
user has since added or renamed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PresetClass:
    """One class in a template list.

    ``class_id`` is the stable identifier a label refers to. It is not a
    position: export assigns contiguous YOLO indices separately, so ids
    stay put when a class is added or removed.
    """

    class_id: int
    name: str


#: The twenty ATCC vehicle categories this project started life with.
ATCC_V1 = [
    PresetClass(1, "Two Wheeler"),
    PresetClass(2, "Three-Wheeler Passenger"),
    PresetClass(3, "Three-Wheeler Freight"),
    PresetClass(4, "Car/Jeep/Van"),
    PresetClass(5, "LCV 2-Axle"),
    PresetClass(6, "LCV 3-Axle"),
    PresetClass(7, "Bus 2-Axle"),
    PresetClass(8, "Bus 3-Axle"),
    PresetClass(9, "Mini-Bus"),
    PresetClass(10, "Truck 2-Axle"),
    PresetClass(11, "Truck 3-Axle"),
    PresetClass(12, "Truck 4-Axle"),
    PresetClass(13, "Truck 5-Axle"),
    PresetClass(14, "Truck 6-Axle"),
    PresetClass(15, "Truck Multi-Axle (7+)"),
    PresetClass(16, "Earth Moving Machinery"),
    PresetClass(17, "Heavy Construction Machinery"),
    PresetClass(18, "Tractor"),
    PresetClass(19, "Tractor with Trailer"),
    PresetClass(20, "Tata Ace / similar mini LCV"),
]

#: Number-plate work. Two classes, because a plate detector only needs to
#: know vehicle from plate - the twenty-way split above is noise here and
#: would spread a small dataset across categories nothing will learn.
ANPR_V1 = [
    PresetClass(1, "vehicle"),
    PresetClass(2, "number_plate"),
]

#: For a project whose classes do not resemble either, so the user is not
#: made to delete twenty rows before adding their own.
BLANK = []

PRESETS: dict[str, list[PresetClass]] = {
    "atcc-v1": ATCC_V1,
    "anpr-v1": ANPR_V1,
    "blank": BLANK,
}

#: What a project gets when nobody chooses - and what every project that
#: predates per-project classes is seeded with, since they were all
#: implicitly on this list.
DEFAULT_PRESET = "atcc-v1"


def get_preset(name: str) -> list[PresetClass]:
    """The template for ``name``. Raises for an unknown preset rather than
    quietly handing back an empty project."""
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(f"Unknown class preset: {name!r}. Expected one of {', '.join(PRESETS)}.") from None
