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
