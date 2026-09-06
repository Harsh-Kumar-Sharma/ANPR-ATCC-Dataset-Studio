import random

SPLIT_TRAIN = "train"
SPLIT_VAL = "val"
SPLIT_TEST = "test"


def compute_split(
    item_ids: list[str],
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    seed: int,
) -> dict[str, str]:
    """Deterministically assign each item to train/val/test.

    Same items + same ratios + same seed always produces the same
    split (docs/01_PRD.md acceptance criterion: "Fixed dataset
    version/config produces deterministic export"). Sorting before
    shuffling removes any dependency on incoming order (e.g. a DB
    query's row order, which SQLite does not guarantee).
    """
    if not abs((train_ratio + val_ratio + test_ratio) - 1.0) < 1e-6:
        raise ValueError("train_ratio + val_ratio + test_ratio must sum to 1.0")

    ordered = sorted(item_ids)
    random.Random(seed).shuffle(ordered)

    n = len(ordered)
    n_train = round(n * train_ratio)
    n_val = round(n * val_ratio)
    # Remainder goes to test, so rounding never drops or duplicates an item.
    n_val = min(n_val, n - n_train)

    split: dict[str, str] = {}
    for item_id in ordered[:n_train]:
        split[item_id] = SPLIT_TRAIN
    for item_id in ordered[n_train : n_train + n_val]:
        split[item_id] = SPLIT_VAL
    for item_id in ordered[n_train + n_val :]:
        split[item_id] = SPLIT_TEST
    return split
