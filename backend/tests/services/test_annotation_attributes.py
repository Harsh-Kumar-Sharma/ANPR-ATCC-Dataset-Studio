"""The attribute validator on its own, branch by branch.

The API-level tests cover the round trip. These cover the rules that
only show up at the edges - retiring an attribute, a boolean's two
representations, and the claim that the served list and the validator
cannot drift apart.
"""

import pytest

from app.core.errors import AppError
from app.services.annotation_attributes import (
    ATTRIBUTE_DEFINITIONS,
    InvalidAttributeError,
    clean_attributes,
)


def _options(key: str) -> list[str]:
    definition = next(d for d in ATTRIBUTE_DEFINITIONS if d["key"] == key)
    return [option["value"] for option in definition["options"]]


# --- the served list and the validator cannot disagree -----------------------


def test_every_option_the_panel_is_offered_can_actually_be_saved():
    """The whole design claim: one list, so a control cannot offer a
    value the save refuses. Checking the shape of the list does not test
    that - this does."""
    for definition in ATTRIBUTE_DEFINITIONS:
        if definition["type"] != "choice":
            continue
        for option in definition["options"]:
            cleaned = clean_attributes({definition["key"]: option["value"]})
            assert cleaned == {definition["key"]: option["value"]}, definition["key"]


def test_the_panel_can_clear_any_attribute():
    """Clearing is what the controls produce for "not set": an empty
    string from a text field or a select, and an absent key from a
    checkbox. None of those may be an error."""
    for definition in ATTRIBUTE_DEFINITIONS:
        if definition["type"] != "boolean":
            assert clean_attributes({definition["key"]: ""}) == {}, definition["key"]
        assert clean_attributes({definition["key"]: None}) == {}, definition["key"]


# --- retiring an attribute must not lock a frame ----------------------------


def test_a_value_left_over_from_a_retired_attribute_is_dropped_not_refused():
    """The canvas loads a box and sends its whole attribute set back. If
    an attribute was retired since, that set carries a key the panel
    cannot render and the user cannot clear - and refusing it makes the
    entire frame unsavable over something invisible."""
    stored = {"retired_thing": "whatever", "plate_text": "MH12AB1234"}

    cleaned = clean_attributes(dict(stored), stored=stored)

    assert cleaned == {"plate_text": "MH12AB1234"}


def test_a_stored_choice_value_that_is_no_longer_an_option_is_dropped_too():
    """Same situation one level down: the key survives, the value does
    not. Removing an option from the list must not lock every frame that
    used it."""
    stored = {"colour": "vermilion"}

    assert clean_attributes(dict(stored), stored=stored) == {}


def test_a_newly_typed_unknown_attribute_is_still_refused():
    """The reason the check exists. A key that is not an echo of what is
    stored is a client writing something nothing will ever read."""
    with pytest.raises(InvalidAttributeError) as raised:
        clean_attributes({"plate_txt": "MH12AB1234"}, stored={"plate_text": "MH12AB1234"})

    assert "plate_txt" in str(raised.value)


def test_a_changed_value_for_a_retired_attribute_is_refused():
    """Dropping is for an echo. Anything else is a client inventing a
    value for a field the server does not have."""
    with pytest.raises(InvalidAttributeError):
        clean_attributes({"retired_thing": "something new"}, stored={"retired_thing": "the old value"})


def test_a_brand_new_box_has_nothing_stored_to_excuse_an_unknown_key():
    with pytest.raises(InvalidAttributeError):
        clean_attributes({"retired_thing": "whatever"})


# --- one representation for "not set" ---------------------------------------


def test_false_is_stored_as_absent_so_a_boolean_has_one_representation():
    """A checkbox has two states, not three: it can produce present-true
    or absent, never an explicit false. Storing false as well would give
    "not occluded" two spellings, and the rule this module states is that
    it gets one."""
    assert clean_attributes({"occluded": False}) == {}
    assert clean_attributes({"occluded": True}) == {"occluded": True}


def test_a_stored_false_is_normalised_away_when_the_box_is_saved_again():
    """Anything written before that rule existed settles on the way
    through, rather than lingering as a second spelling forever."""
    stored = {"night": False}

    assert clean_attributes(dict(stored), stored=stored) == {}


def test_a_boolean_still_refuses_things_that_are_not_booleans():
    for value in ("true", 1, 0, "", []):
        with pytest.raises(InvalidAttributeError):
            clean_attributes({"occluded": value})


# --- plate text matches the plate text the rest of the app stores ------------


def test_plate_text_is_canonicalised_the_same_way_ocr_text_is():
    """The OCR card stores `normalize_plate_text` output. If the canvas
    stored raw typing, the same vehicle's plate would never compare equal
    across the two, which is the whole reason to record it in one place."""
    from app.services.text_normalization import normalize_plate_text

    typed = "mh 12 ab 1234"

    assert clean_attributes({"plate_text": typed}) == {"plate_text": normalize_plate_text(typed)}
    assert clean_attributes({"plate_text": typed})["plate_text"] == "MH12AB1234"


def test_plate_text_that_normalises_to_nothing_is_cleared():
    assert clean_attributes({"plate_text": "---"}) == {}


def test_the_length_limit_applies_to_the_canonical_form():
    """Otherwise the limit would be about how someone spaces a plate
    rather than about the plate."""
    spaced = " ".join("X" * 32)  # 63 characters typed, 32 once canonicalised

    assert clean_attributes({"plate_text": spaced}) == {"plate_text": "X" * 32}

    with pytest.raises(InvalidAttributeError):
        clean_attributes({"plate_text": "Y" * 33})


# --- fitting the codebase ----------------------------------------------------


def test_an_attribute_error_is_an_app_error_like_every_other_service_error():
    """Every other service raises `AppError`, which the handlers turn
    into a 400 with a code. A bare ValueError would reach the catch-all
    and become an opaque 500 the first time a caller forgot to wrap it."""
    assert issubclass(InvalidAttributeError, AppError)
    assert InvalidAttributeError("x").code == "invalid_attribute"
