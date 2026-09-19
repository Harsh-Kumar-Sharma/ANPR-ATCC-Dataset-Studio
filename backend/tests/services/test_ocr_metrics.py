"""What the model read, against what a person wrote down.

These used to be written against a single ``ocr_candidates`` table where
a human correction was another row and the metric compared the model's
best attempt with whichever row was ``selected``. Running OCR selects
the model's own best attempt, so a track nobody had looked at compared
the model against itself and scored an agreement - the rate was mostly a
measure of how little reviewing had been done. A human's reading lives
on the annotation now, so the two sides are genuinely different.
"""

from app.services.ocr_metrics import ModelReading, compute_ocr_agreement


def test_a_human_who_wrote_down_what_the_model_read_is_an_agreement():
    result = compute_ocr_agreement(
        {"track-1": [ModelReading("MH12AB1234", 0.9)]},
        {"track-1": "MH12AB1234"},
    )

    assert result == {"tracks_with_ocr": 1, "agreements": 1, "corrections": 0, "agreement_rate": 1.0}


def test_a_human_who_wrote_down_something_else_is_a_correction():
    result = compute_ocr_agreement(
        {"track-1": [ModelReading("MH12AB1234", 0.9)]},
        {"track-1": "MH12AB1235"},
    )

    assert result["agreements"] == 0
    assert result["corrections"] == 1
    assert result["agreement_rate"] == 0.0


def test_a_track_nobody_has_read_is_not_counted_as_the_model_being_right():
    """The old metric's central flaw. Running OCR marks the model's own
    best attempt as selected, so an untouched track compared the model
    with itself and every one of them counted as a success."""
    result = compute_ocr_agreement({"track-1": [ModelReading("MH12AB1234", 0.9)]}, {})

    assert result["tracks_with_ocr"] == 0
    assert result["agreements"] == 0
    assert result["agreement_rate"] is None


def test_a_track_the_model_could_not_read_is_not_counted_either():
    """There is nothing for it to have been right or wrong about."""
    result = compute_ocr_agreement({"track-1": []}, {"track-1": "MH12AB1234"})

    assert result["tracks_with_ocr"] == 0
    assert result["agreement_rate"] is None


def test_the_models_most_confident_reading_is_the_one_compared():
    result = compute_ocr_agreement(
        {"track-1": [ModelReading("WRONG", 0.3), ModelReading("MH12AB1234", 0.9)]},
        {"track-1": "MH12AB1234"},
    )

    assert result["agreements"] == 1


def test_an_empty_plate_is_not_a_reading():
    """Clearing a plate is not the human saying the model was wrong; it
    is the human saying nothing."""
    result = compute_ocr_agreement({"track-1": [ModelReading("MH12AB1234", 0.9)]}, {"track-1": ""})

    assert result["tracks_with_ocr"] == 0


def test_no_tracks_at_all():
    result = compute_ocr_agreement({}, {})

    assert result == {"tracks_with_ocr": 0, "agreements": 0, "corrections": 0, "agreement_rate": None}


def test_a_mix_of_tracks_gives_the_rate_over_the_ones_that_count():
    result = compute_ocr_agreement(
        {
            "t1": [ModelReading("AAA", 0.9)],
            "t2": [ModelReading("BBB", 0.9)],
            "t3": [ModelReading("DDD", 0.9)],
            "t4": [ModelReading("EEE", 0.9)],
            "t5": [ModelReading("GGG", 0.9)],  # nobody read this one
        },
        {"t1": "AAA", "t2": "CCC", "t3": "DDD", "t4": "FFF"},
    )

    assert result["tracks_with_ocr"] == 4
    assert result["agreements"] == 2
    assert result["corrections"] == 2
    assert result["agreement_rate"] == 0.5
