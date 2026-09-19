"""Which frames are worth a human's time.

Detection samples frames at a few per second, so a vehicle crossing a
gantry produces a dozen near-identical shots of the same lane. Labelling
all twelve costs twelve times as much and teaches the model almost
nothing the first one did not.

The rule here is deliberately simple: drop what is too poor to label,
then drop what is too similar to something already kept. Uncertainty
sampling - "show me the frames the model finds confusing" - is the
version that actually compounds, and it needs a custom model to be
uncertain, which does not exist yet.

Quality reuses the existing scoring rather than inventing another one:
detection stored the signals (blur, area, confidence, truncation) on
each frame-candidate row, and this feeds them back through
``frame_ranking.compute_composite_score``. A frame is as good as its
best detection.

Brightness and vehicle count are mostly not filters - they decide what
counts as a *duplicate*: two frames that hash alike are the same scene
only if they hold the same number of vehicles under similar light. A
lane at noon and the same lane at night are two training examples. The
one exception is a frame with nothing detected on it at all, which is
dropped outright.

Measured on real gantry footage (3,842 frames of ``atcc1.mp4``, see
``scripts/selection_report.py``):

* consecutive frames differ by a median of 0 bits, p90 of 2;
* any two frames of the clip differ by a median of only 6, p10 of 3.

That narrow gap is the whole difficulty. The camera is fixed, so the
static background dominates the hash and two completely different
vehicles can be 6 bits apart. The default threshold sits between those
two distributions - above the consecutive p90, below the any-two p10 -
which is why it is 2 and not something rounder. At 2 that clip offers
338 of its 3,842 frames; at 6 it offered 43, which is not a dataset.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from app.services.frame_ranking import DEFAULT_RANKING_CONFIG, RankingConfig, compute_composite_score

#: dhash compares each pixel with its right-hand neighbour on a small
#: grid. 8x9 greyscale gives the 64 comparisons that make the hash.
_HASH_WIDTH = 9
_HASH_HEIGHT = 8


@dataclass(frozen=True)
class FrameSignals:
    """Everything selection needs to judge one frame.

    Gathered by the caller, because reading them means decoding the
    frame and this module should stay testable without pixels.
    """

    frame_id: str
    frame_index: int
    #: 0-1, the best of this frame's detections, from the existing scores.
    quality: float
    vehicle_count: int
    #: 0-1 mean luma.
    brightness: float
    #: 64-bit perceptual hash.
    phash: int


@dataclass(frozen=True)
class SelectionConfig:
    """Thresholds. Starting points, not calibrated against real gantry
    footage - the same caveat the ranking config carries."""

    #: Below this a frame is too poor to be worth drawing on. Rarely
    #: fires in practice: the detector's own confidence gate has already
    #: thrown out the worst observations before they reach here, and the
    #: real footage measured spans 0.22-0.65. It is a floor against
    #: genuinely broken frames, not the main filter.
    min_quality: float = 0.2
    #: Hashes within this many bits are the same scene. Measured, not
    #: guessed - see the module docstring.
    hamming_threshold: int = 2
    #: Vehicle counts further apart than this make two frames different
    #: scenes however alike they look.
    vehicle_count_tolerance: int = 0
    #: Brightness further apart than this does the same. On a fixed
    #: camera under steady light this never discriminates (the footage
    #: measured spans 0.39-0.45); it earns its keep on a source that
    #: runs into dusk.
    brightness_tolerance: float = 0.15


DEFAULT_SELECTION_CONFIG = SelectionConfig()


@dataclass(frozen=True)
class SelectionDecision:
    """What was decided about one frame, and why.

    The reason is the whole point of recording anything: a queue full of
    the wrong frames is only fixable if each one can say how it got in.
    """

    frame_id: str
    selected: bool
    reason: str


def frame_quality(candidates: list, config: RankingConfig = DEFAULT_RANKING_CONFIG) -> float:
    """How good the best look at anything on this frame is.

    Not recomputed from pixels: detection already scored every
    observation, and those scores are on the frame-candidate rows. A
    frame is worth as much as its best detection - one sharp vehicle
    makes a frame worth labelling even if the others are a blur.

    Candidates from before quality signals existed have null scores;
    they are treated as neutral rather than dropped, because "we did not
    measure this" is not the same as "this is bad".
    """
    scores = [
        compute_composite_score(
            blur_score=candidate.blur_score if candidate.blur_score is not None else 0.5,
            area_ratio=candidate.area_ratio if candidate.area_ratio is not None else 0.5,
            confidence=candidate.detector_confidence,
            # Stability compares a detection with its neighbours inside
            # one track, which is not a question a single frame can
            # answer. 1.0 is the neutral value - it is what
            # compute_temporal_stability returns for an isolated or
            # perfectly steady detection - so a frame is not quietly
            # penalised for being judged on its own.
            temporal_stability=1.0,
            truncated=bool((candidate.flags_json or {}).get("truncated")),
            config=config,
        )
        for candidate in candidates
    ]
    return max(scores) if scores else 0.0


def brightness_of(image: np.ndarray) -> float:
    """Mean luma, 0-1."""
    return float(image.mean()) / 255.0


def perceptual_hash(image: np.ndarray) -> int:
    """A 64-bit dhash of a frame.

    Rows of "is this pixel brighter than the one to its right", which
    survives compression noise and small exposure shifts but changes
    when the scene does.
    """
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    # INTER_AREA is an area average, which is the block average this
    # wants - and roughly seven times faster than doing it by hand in
    # numpy, which matters when it runs on every frame of a source. It
    # also handles sizes smaller than the grid, where hand-rolled block
    # splitting produced empty slices and silent NaNs.
    small = cv2.resize(grey, (_HASH_WIDTH, _HASH_HEIGHT), interpolation=cv2.INTER_AREA).astype(np.int32)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming_distance(left: int, right: int) -> int:
    """How many bits two hashes differ in."""
    return bin(left ^ right).count("1")


def select_frames(
    signals: list[FrameSignals], config: SelectionConfig = DEFAULT_SELECTION_CONFIG
) -> list[SelectionDecision]:
    """Decide which frames belong in the labelling queue.

    Judged in frame order so a re-run over the same source makes the
    same choices, and so the frame kept out of a run of similar ones is
    the first, not whichever happened to arrive first.

    Similarity is measured against what was *kept*, never against what
    was merely seen - otherwise a long slow pan leaks through one hop at
    a time, each frame near enough to its neighbour while the run as a
    whole drifts anywhere.
    """
    decisions: list[SelectionDecision] = []
    kept: list[FrameSignals] = []

    for frame in sorted(signals, key=lambda f: f.frame_index):
        if frame.vehicle_count == 0:
            decisions.append(SelectionDecision(frame.frame_id, False, "no detections on this frame"))
            continue

        if frame.quality < config.min_quality:
            decisions.append(
                SelectionDecision(frame.frame_id, False, f"quality {frame.quality:.2f} below {config.min_quality:.2f}")
            )
            continue

        duplicate_of = _duplicate_among(frame, kept, config)
        if duplicate_of is not None:
            decisions.append(
                SelectionDecision(frame.frame_id, False, f"near-duplicate of frame {duplicate_of.frame_index}")
            )
            continue

        kept.append(frame)
        decisions.append(
            SelectionDecision(
                frame.frame_id,
                True,
                f"quality {frame.quality:.2f}, {frame.vehicle_count} vehicle(s), brightness {frame.brightness:.2f}",
            )
        )

    return decisions


def _duplicate_among(
    frame: FrameSignals, kept: list[FrameSignals], config: SelectionConfig
) -> FrameSignals | None:
    for other in kept:
        if abs(frame.vehicle_count - other.vehicle_count) > config.vehicle_count_tolerance:
            continue
        if abs(frame.brightness - other.brightness) > config.brightness_tolerance:
            continue
        if hamming_distance(frame.phash, other.phash) <= config.hamming_threshold:
            return other
    return None
