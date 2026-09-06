import pytest

from app.services.frame_ranking import (
    RankingConfig,
    classify_bucket,
    compute_composite_score,
    compute_temporal_stability,
    select_roles_by_score,
)


def test_temporal_stability_is_perfect_for_uniform_confidences():
    confidences = [0.9, 0.9, 0.9, 0.9]
    for i in range(len(confidences)):
        assert compute_temporal_stability(confidences, i) == pytest.approx(1.0)


def test_temporal_stability_penalizes_an_outlier_frame():
    confidences = [0.9, 0.9, 0.1, 0.9, 0.9]
    stability_of_outlier = compute_temporal_stability(confidences, 2)
    stability_of_normal = compute_temporal_stability(confidences, 0)
    assert stability_of_outlier < stability_of_normal


def test_temporal_stability_handles_single_frame_track():
    assert compute_temporal_stability([0.9], 0) == 1.0


def test_composite_score_rewards_sharp_large_confident_stable_frames():
    good = compute_composite_score(
        blur_score=0.1, area_ratio=0.8, confidence=0.9, temporal_stability=1.0, truncated=False
    )
    bad = compute_composite_score(
        blur_score=0.9, area_ratio=0.1, confidence=0.3, temporal_stability=0.2, truncated=False
    )
    assert good > bad
    assert 0.0 <= bad <= good <= 1.0


def test_truncation_penalizes_the_score():
    config = RankingConfig()
    kwargs = dict(blur_score=0.1, area_ratio=0.8, confidence=0.9, temporal_stability=1.0)
    untruncated = compute_composite_score(**kwargs, truncated=False, config=config)
    truncated = compute_composite_score(**kwargs, truncated=True, config=config)
    assert truncated == pytest.approx(untruncated * config.truncated_penalty)


def test_classify_bucket_thresholds():
    config = RankingConfig(hard_threshold=0.5, failed_threshold=0.2)
    assert classify_bucket(0.9, config) == "BEST_DETECTION"
    assert classify_bucket(0.5, config) == "BEST_DETECTION"
    assert classify_bucket(0.35, config) == "HARD"
    assert classify_bucket(0.2, config) == "HARD"
    assert classify_bucket(0.1, config) == "FAILED"
    assert classify_bucket(0.0, config) == "FAILED"


def test_select_roles_by_score_marks_single_best_and_bounded_ocr_shortlist():
    config = RankingConfig(ocr_candidate_count=2)
    scores = [0.9, 0.5, 0.7, 0.1]

    roles = select_roles_by_score(scores, config)

    best_detection_indices = [i for i, r in enumerate(roles) if "best_detection" in r]
    ocr_indices = [i for i, r in enumerate(roles) if "ocr_candidate" in r]

    assert best_detection_indices == [0]  # highest score
    assert set(ocr_indices) == {0, 2}  # top 2 scores: index 0 (0.9), index 2 (0.7)


def test_select_roles_by_score_handles_empty_list():
    assert select_roles_by_score([]) == []
