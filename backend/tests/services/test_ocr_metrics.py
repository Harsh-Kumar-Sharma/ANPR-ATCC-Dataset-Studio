from app.services.ocr_metrics import OcrAttempt, compute_ocr_agreement


def test_track_with_unchanged_model_selection_counts_as_agreement():
    attempts = {
        "track-1": [
            OcrAttempt(source="model", normalized_text="MH12AB1234", confidence=0.9, selected=True),
        ]
    }
    result = compute_ocr_agreement(attempts)
    assert result == {"tracks_with_ocr": 1, "agreements": 1, "corrections": 0, "agreement_rate": 1.0}


def test_track_with_human_correction_counts_as_a_correction():
    attempts = {
        "track-1": [
            OcrAttempt(source="model", normalized_text="MH12AB1234", confidence=0.9, selected=False),
            OcrAttempt(source="human", normalized_text="MH12AB1235", confidence=1.0, selected=True),
        ]
    }
    result = compute_ocr_agreement(attempts)
    assert result["tracks_with_ocr"] == 1
    assert result["agreements"] == 0
    assert result["corrections"] == 1
    assert result["agreement_rate"] == 0.0


def test_human_reselecting_the_same_text_still_counts_as_agreement():
    attempts = {
        "track-1": [
            OcrAttempt(source="model", normalized_text="MH12AB1234", confidence=0.9, selected=False),
            OcrAttempt(source="human", normalized_text="MH12AB1234", confidence=1.0, selected=True),
        ]
    }
    result = compute_ocr_agreement(attempts)
    assert result["agreements"] == 1
    assert result["corrections"] == 0


def test_top_model_attempt_by_confidence_is_the_one_compared():
    attempts = {
        "track-1": [
            OcrAttempt(source="model", normalized_text="WRONG", confidence=0.3, selected=False),
            OcrAttempt(source="model", normalized_text="MH12AB1234", confidence=0.9, selected=True),
        ]
    }
    result = compute_ocr_agreement(attempts)
    assert result["agreements"] == 1


def test_track_with_no_model_attempts_is_excluded():
    attempts = {"track-1": [OcrAttempt(source="human", normalized_text="X", confidence=1.0, selected=True)]}
    result = compute_ocr_agreement(attempts)
    assert result["tracks_with_ocr"] == 0
    assert result["agreement_rate"] is None


def test_no_tracks_at_all():
    result = compute_ocr_agreement({})
    assert result == {"tracks_with_ocr": 0, "agreements": 0, "corrections": 0, "agreement_rate": None}


def test_mixed_tracks_compute_correct_rate():
    attempts = {
        "t1": [OcrAttempt("model", "AAA", 0.9, True)],
        "t2": [OcrAttempt("model", "BBB", 0.9, False), OcrAttempt("human", "CCC", 1.0, True)],
        "t3": [OcrAttempt("model", "DDD", 0.9, True)],
        "t4": [OcrAttempt("model", "EEE", 0.9, False), OcrAttempt("human", "FFF", 1.0, True)],
    }
    result = compute_ocr_agreement(attempts)
    assert result["tracks_with_ocr"] == 4
    assert result["agreements"] == 2
    assert result["corrections"] == 2
    assert result["agreement_rate"] == 0.5
