import pytest

from app.services.dataset_split import SPLIT_TEST, SPLIT_TRAIN, SPLIT_VAL, compute_split


def test_split_is_deterministic_for_the_same_seed():
    items = [f"item-{i}" for i in range(50)]
    first = compute_split(items, 0.8, 0.1, 0.1, seed=42)
    second = compute_split(items, 0.8, 0.1, 0.1, seed=42)
    assert first == second


def test_different_seeds_can_produce_different_splits():
    items = [f"item-{i}" for i in range(50)]
    a = compute_split(items, 0.8, 0.1, 0.1, seed=1)
    b = compute_split(items, 0.8, 0.1, 0.1, seed=2)
    assert a != b


def test_split_covers_every_item_exactly_once():
    items = [f"item-{i}" for i in range(37)]
    split = compute_split(items, 0.8, 0.1, 0.1, seed=7)
    assert set(split.keys()) == set(items)
    assert all(v in (SPLIT_TRAIN, SPLIT_VAL, SPLIT_TEST) for v in split.values())


def test_split_respects_approximate_ratios():
    items = [f"item-{i}" for i in range(1000)]
    split = compute_split(items, 0.8, 0.1, 0.1, seed=3)
    counts = {SPLIT_TRAIN: 0, SPLIT_VAL: 0, SPLIT_TEST: 0}
    for v in split.values():
        counts[v] += 1
    assert 780 <= counts[SPLIT_TRAIN] <= 820
    assert 80 <= counts[SPLIT_VAL] <= 120
    assert 80 <= counts[SPLIT_TEST] <= 120


def test_input_order_does_not_affect_result():
    items = [f"item-{i}" for i in range(20)]
    a = compute_split(items, 0.8, 0.1, 0.1, seed=9)
    b = compute_split(list(reversed(items)), 0.8, 0.1, 0.1, seed=9)
    assert a == b


def test_ratios_must_sum_to_one():
    with pytest.raises(ValueError):
        compute_split(["a", "b"], 0.5, 0.3, 0.3, seed=1)


def test_small_item_count_never_drops_or_duplicates():
    for n in range(0, 6):
        items = [f"item-{i}" for i in range(n)]
        split = compute_split(items, 0.8, 0.1, 0.1, seed=5)
        assert len(split) == n


# --- splitting by passage -----------------------------------------------------

from app.services.dataset_split import compute_passage_split, group_into_passages  # noqa: E402


def _passages(count: int, frames_each: int, source: str = "s1", gap_ms: int = 60_000) -> list[tuple[str, str, int]]:
    """``count`` vehicles, ``frames_each`` frames 100 ms apart, a minute between vehicles."""
    return [
        (f"{source}-v{v}-f{f}", source, v * gap_ms + f * 100)
        for v in range(count)
        for f in range(frames_each)
    ]


def test_consecutive_frames_are_one_passage():
    passages = group_into_passages(_passages(2, 3))
    assert len(set(passages.values())) == 2
    assert passages["s1-v0-f0"] == passages["s1-v0-f2"]
    assert passages["s1-v0-f0"] != passages["s1-v1-f0"]


def test_different_sources_are_different_passages_even_at_the_same_time():
    frames = _passages(1, 3, "a") + _passages(1, 3, "b")
    assert len(set(group_into_passages(frames).values())) == 2


def test_a_vehicle_never_straddles_splits():
    frames = _passages(40, 3)
    split, passage_count = compute_passage_split(frames, 0.75, 0.15, 0.10, seed=1)

    assert passage_count == 40
    for v in range(40):
        assert len({split[f"s1-v{v}-f{f}"] for f in range(3)}) == 1


def test_the_passage_split_is_reproducible():
    frames = _passages(30, 2)
    first, _ = compute_passage_split(frames, 0.75, 0.15, 0.10, seed=9)
    second, _ = compute_passage_split(list(reversed(frames)), 0.75, 0.15, 0.10, seed=9)
    assert first == second


def test_every_split_gets_passages_in_proportion():
    split, _ = compute_passage_split(_passages(100, 2), 0.75, 0.15, 0.10, seed=3)
    by_split = {s: sum(1 for v in split.values() if v == s) for s in ("train", "val", "test")}
    assert by_split == {"train": 150, "val": 30, "test": 20}
