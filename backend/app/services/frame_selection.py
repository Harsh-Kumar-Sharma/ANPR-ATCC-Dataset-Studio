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

Quality is not recomputed. Detection already scored every observation
with ``frame_ranking.compute_composite_score``, and those scores are on
the frame-candidate rows; a frame is as good as its best detection.

Brightness and vehicle count are not filters. They decide what counts
as a duplicate: two frames that hash alike are only the same scene if
they also hold the same number of vehicles under similar light. A lane
at noon and the same lane at night are two training examples, not one.
"""

from dataclasses import dataclass

import numpy as np

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

    #: Below this a frame is too poor to be worth drawing on.
    min_quality: float = 0.2
    #: Hashes within this many bits are the same scene.
    hamming_threshold: int = 6
    #: Vehicle counts further apart than this make two frames different
    #: scenes however alike they look.
    vehicle_count_tolerance: int = 0
    #: Brightness further apart than this does the same.
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


def perceptual_hash(image: np.ndarray) -> int:
    """A 64-bit dhash of a frame.

    Rows of "is this pixel brighter than the one to its right", which
    survives compression noise and small exposure shifts but changes
    when the scene does.
    """
    grey = image.mean(axis=2) if image.ndim == 3 else image
    # Block-average down to the hash grid. Cheaper than a resize and it
    # keeps this module free of an image library.
    rows = np.array_split(grey, _HASH_HEIGHT, axis=0)
    small = np.array([[block[:, cols].mean() for cols in np.array_split(np.arange(grey.shape[1]), _HASH_WIDTH)] for block in rows])
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
