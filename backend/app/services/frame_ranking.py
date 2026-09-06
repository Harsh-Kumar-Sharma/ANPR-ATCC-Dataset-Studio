from dataclasses import dataclass


@dataclass(frozen=True)
class RankingConfig:
    """Weights/thresholds for representative-frame ranking and
    hard/failed classification.

    Defaults are reasonable starting points, not calibrated against
    real gantry footage - see docs/HANDOFF.md known issues (same
    caveat as the detector confidence threshold and processing FPS).
    """

    sharpness_weight: float = 0.35
    area_weight: float = 0.25
    confidence_weight: float = 0.25
    temporal_stability_weight: float = 0.15
    truncated_penalty: float = 0.5  # multiplicative

    #: composite score >= hard_threshold -> BEST_DETECTION (usable track)
    hard_threshold: float = 0.5
    #: failed_threshold <= composite score < hard_threshold -> HARD (difficult but real)
    #: composite score < failed_threshold -> FAILED (pipeline couldn't confidently process)
    failed_threshold: float = 0.2

    ocr_candidate_count: int = 3


DEFAULT_RANKING_CONFIG = RankingConfig()

BUCKET_BEST_DETECTION = "BEST_DETECTION"
BUCKET_HARD = "HARD"
BUCKET_FAILED = "FAILED"

ROLE_BEST_DETECTION = "best_detection"
ROLE_OCR_CANDIDATE = "ocr_candidate"


def compute_temporal_stability(confidences: list[float], index: int, window: int = 3) -> float:
    """1.0 = this frame's confidence matches its temporal neighbors in
    the same track; lower means it's an outlier (possible flicker or
    an uncertain detection) relative to nearby frames."""
    lo = max(0, index - window)
    hi = min(len(confidences), index + window + 1)
    neighborhood = confidences[lo:hi]
    if len(neighborhood) <= 1:
        return 1.0
    local_mean = sum(neighborhood) / len(neighborhood)
    if local_mean <= 1e-6:
        return 1.0
    deviation = abs(confidences[index] - local_mean) / local_mean
    return max(0.0, 1.0 - min(1.0, deviation))


def compute_composite_score(
    blur_score: float,
    area_ratio: float,
    confidence: float,
    temporal_stability: float,
    truncated: bool,
    config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> float:
    sharpness_norm = 1.0 - blur_score
    score = (
        sharpness_norm * config.sharpness_weight
        + area_ratio * config.area_weight
        + confidence * config.confidence_weight
        + temporal_stability * config.temporal_stability_weight
    )
    if truncated:
        score *= config.truncated_penalty
    return max(0.0, min(1.0, score))


def classify_bucket(best_score: float, config: RankingConfig = DEFAULT_RANKING_CONFIG) -> str:
    if best_score >= config.hard_threshold:
        return BUCKET_BEST_DETECTION
    if best_score >= config.failed_threshold:
        return BUCKET_HARD
    return BUCKET_FAILED


def select_roles_by_score(scores: list[float], config: RankingConfig = DEFAULT_RANKING_CONFIG) -> list[set[str]]:
    """Given composite scores in track frame-order, return the set of
    roles (best_detection/ocr_candidate) each frame earns.

    The single highest-scoring frame becomes the BEST_DETECTION
    representative. The top ``ocr_candidate_count`` frames (which may
    include the same frame) become OCR candidates for Phase 5 to
    choose among - no OCR runs yet, this only shortlists frames worth
    trying it on.
    """
    if not scores:
        return []

    ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    best_index = ranked_indices[0]
    ocr_indices = set(ranked_indices[: config.ocr_candidate_count])

    roles: list[set[str]] = [set() for _ in scores]
    roles[best_index].add(ROLE_BEST_DETECTION)
    for i in ocr_indices:
        roles[i].add(ROLE_OCR_CANDIDATE)
    return roles
