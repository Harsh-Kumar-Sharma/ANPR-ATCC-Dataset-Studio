import pytest

from app.services.text_normalization import normalize_plate_text


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MH12AB1234", "MH12AB1234"),
        ("mh12ab1234", "MH12AB1234"),
        ("MH 12 AB 1234", "MH12AB1234"),
        ("MH-12-AB-1234", "MH12AB1234"),
        (" MH12AB1234 ", "MH12AB1234"),
        ("", ""),
    ],
)
def test_normalize_plate_text(raw, expected):
    assert normalize_plate_text(raw) == expected
