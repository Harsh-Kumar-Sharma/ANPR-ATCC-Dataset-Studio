import numpy as np
import pytest

from app.services.frame_selection import (
    DEFAULT_SELECTION_CONFIG,
    FrameSignals,
    SelectionConfig,
    hamming_distance,
    perceptual_hash,
    select_frames,
)


def signals(index: int, *, quality: float = 0.8, vehicles: int = 2, brightness: float = 0.5, phash: int = 0) -> FrameSignals:
    return FrameSignals(
        frame_id=f"f-{index}",
        frame_index=index,
        quality=quality,
        vehicle_count=vehicles,
        brightness=brightness,
        phash=phash,
    )


def decisions_by_id(result):
    return {d.frame_id: d for d in result}


# --- the hash ------------------------------------------------------------------


def test_the_same_image_hashes_the_same():
    rng = np.random.default_rng(0)
    image = rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)

    assert perceptual_hash(image) == perceptual_hash(image.copy())


def test_a_slightly_different_image_hashes_close():
    """The point of a perceptual hash: two frames a tenth of a second
    apart differ in pixels but not in content."""
    rng = np.random.default_rng(1)
    image = rng.integers(0, 200, (48, 64, 3), dtype=np.uint8)
    nudged = np.clip(image.astype(int) + rng.integers(-6, 6, image.shape), 0, 255).astype(np.uint8)

    assert hamming_distance(perceptual_hash(image), perceptual_hash(nudged)) <= 8


def test_a_very_different_image_hashes_far():
    # dhash reads left-to-right brightness changes, so the two images
    # have to differ along that axis to be told apart.
    columns = np.arange(64, dtype=np.uint8) * 4
    brightening = np.repeat(columns[None, :, None], 48, axis=0).repeat(3, axis=2)
    darkening = brightening[:, ::-1, :]

    assert hamming_distance(perceptual_hash(brightening), perceptual_hash(darkening)) > 8


def test_the_hash_only_reads_left_to_right_changes():
    """A known limit, recorded rather than discovered later: a flat image
    and one that only darkens rightwards both hash to zero, because
    neither ever gets brighter going right. Real footage has texture, so
    this costs nothing in practice - but it is why two frames that differ
    only in vertical structure are not told apart."""
    flat = np.full((48, 64, 3), 128, dtype=np.uint8)
    darkening = np.repeat((np.arange(64, dtype=np.uint8) * 4)[None, ::-1, None], 48, axis=0).repeat(3, axis=2)

    assert perceptual_hash(flat) == perceptual_hash(darkening) == 0


def test_a_flat_image_is_handled_without_blowing_up():
    flat = np.full((48, 64, 3), 128, dtype=np.uint8)

    assert isinstance(perceptual_hash(flat), int)


def test_hamming_distance_counts_differing_bits():
    assert hamming_distance(0b1011, 0b1001) == 1
    assert hamming_distance(0, 0) == 0


# --- selection -----------------------------------------------------------------


def test_a_sharp_varied_frame_is_kept_and_says_why():
    result = select_frames([signals(0, quality=0.9, vehicles=3)])

    assert len(result) == 1
    assert result[0].selected is True
    assert "0.9" in result[0].reason
    assert "3" in result[0].reason


def test_a_frame_below_the_quality_floor_is_skipped():
    result = decisions_by_id(select_frames([signals(0, quality=0.05)]))

    assert result["f-0"].selected is False
    assert "quality" in result["f-0"].reason.lower()


def test_a_frame_with_no_detections_is_skipped():
    """A queue of empty frames is exactly not worth a human's time. The
    frames where the detector missed something are the interesting ones,
    but finding those needs a model to be uncertain - ticket 11 says
    that is out of scope."""
    result = decisions_by_id(select_frames([signals(0, vehicles=0, quality=0.0)]))

    assert result["f-0"].selected is False
    assert "no detections" in result["f-0"].reason.lower()


def test_a_near_duplicate_of_a_kept_frame_is_skipped():
    result = decisions_by_id(select_frames([signals(0, phash=0b1010), signals(1, phash=0b1011)]))

    assert result["f-0"].selected is True
    assert result["f-1"].selected is False
    assert "duplicate" in result["f-1"].reason.lower()


def test_the_skipped_duplicate_names_the_frame_it_duplicates():
    """"Debuggable rather than guessed at" means the reason has to point
    somewhere."""
    result = decisions_by_id(select_frames([signals(4, phash=0b1010), signals(9, phash=0b1011)]))

    assert "frame 4" in result["f-9"].reason


def test_a_frame_that_looks_the_same_but_holds_more_vehicles_is_kept():
    """Vehicle count is what makes two similar-looking frames different
    scenes rather than the same one twice."""
    result = decisions_by_id(select_frames([signals(0, phash=0b1010, vehicles=1), signals(1, phash=0b1011, vehicles=4)]))

    assert result["f-0"].selected is True
    assert result["f-1"].selected is True


def test_a_frame_that_looks_the_same_under_very_different_light_is_kept():
    """Day and night shots of the same lane are not the same training
    example."""
    result = decisions_by_id(
        select_frames([signals(0, phash=0b1010, brightness=0.2), signals(1, phash=0b1011, brightness=0.8)])
    )

    assert result["f-0"].selected is True
    assert result["f-1"].selected is True


def test_duplicates_are_measured_against_what_was_kept_not_what_was_seen():
    """Three frames drifting slowly apart: the third must be compared
    with the first (which was kept), not the second (which was not), or
    a long run of near-identical frames leaks through one hop at a time."""
    result = decisions_by_id(
        select_frames(
            [signals(0, phash=0b0000), signals(1, phash=0b0001), signals(2, phash=0b0011)],
            SelectionConfig(hamming_threshold=2),
        )
    )

    assert result["f-0"].selected is True
    assert result["f-1"].selected is False
    assert result["f-2"].selected is False


def test_a_frame_far_enough_from_everything_kept_is_kept():
    result = decisions_by_id(
        select_frames(
            [signals(0, phash=0b0000), signals(1, phash=0b1111_1111)],
            SelectionConfig(hamming_threshold=2),
        )
    )

    assert all(d.selected for d in result.values())


def test_frames_are_judged_in_frame_order_whatever_order_they_arrive_in():
    """The earlier frame is the one kept, so a re-run over the same source
    makes the same choices."""
    later = signals(9, phash=0b1010)
    earlier = signals(2, phash=0b1011)

    result = decisions_by_id(select_frames([later, earlier]))

    assert result["f-2"].selected is True
    assert result["f-9"].selected is False


def test_every_frame_gets_a_decision():
    result = select_frames([signals(i) for i in range(5)])

    assert len(result) == 5
    assert all(d.reason for d in result)


def test_selection_over_a_repetitive_run_keeps_only_a_fraction():
    """The ticket's done-when: a queue of near-identical frames comes out
    visibly shorter."""
    repetitive = [signals(i, phash=0b1010_1010 | (i % 2)) for i in range(40)]

    kept = [d for d in select_frames(repetitive) if d.selected]

    assert len(kept) == 1


def test_a_varied_run_is_left_alone():
    # Genuinely spread-out hashes: two hashes with only a couple of bits
    # set are always close, whatever bits they are.
    rng = np.random.default_rng(7)
    varied = [signals(i, phash=int(rng.integers(0, 2**63))) for i in range(20)]

    kept = [d for d in select_frames(varied) if d.selected]

    assert len(kept) >= 15


def test_nothing_in_produces_nothing_out():
    assert select_frames([]) == []


def test_the_default_config_is_usable_as_given():
    assert DEFAULT_SELECTION_CONFIG.hamming_threshold > 0
    assert 0 < DEFAULT_SELECTION_CONFIG.min_quality < 1


@pytest.mark.parametrize("threshold", [0, 1, 64])
def test_any_sane_threshold_still_decides_every_frame(threshold):
    result = select_frames([signals(i, phash=i) for i in range(6)], SelectionConfig(hamming_threshold=threshold))

    assert len(result) == 6
