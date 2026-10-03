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


#: Frames of one source closer together than this are one passage - the
#: same vehicle, or the same few seconds of traffic.
PASSAGE_GAP_MS = 3000


def group_into_passages(frames: list[tuple[str, str, int]], gap_ms: int = PASSAGE_GAP_MS) -> dict[str, str]:
    """Group frames into passages: runs of one source's frames with no
    gap longer than ``gap_ms`` between neighbours.

    ``frames`` is ``(frame_id, source_id, timestamp_ms)``. Returns each
    frame's passage id. A vehicle in view for a few seconds is a few
    consecutive frames of one source, so it lands in one passage.
    """
    passages: dict[str, str] = {}
    ordered = sorted(frames, key=lambda f: (f[1], f[2], f[0]))
    current: str | None = None
    previous: tuple[str, int] | None = None
    for frame_id, source_id, timestamp_ms in ordered:
        if previous is None or previous[0] != source_id or timestamp_ms - previous[1] > gap_ms:
            current = f"{source_id}:{timestamp_ms}"
        passages[frame_id] = current  # type: ignore[assignment]
        previous = (source_id, timestamp_ms)
    return passages


def compute_passage_split(
    frames: list[tuple[str, str, int]],
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    seed: int,
    gap_ms: int = PASSAGE_GAP_MS,
) -> tuple[dict[str, str], int]:
    """Split by passage, so one vehicle's frames never straddle train
    and val.

    A frame-by-frame split put near-identical frames of the same car in
    both, and validation then measured how well the model remembered
    pictures it had trained on - a high mAP that meant little. Returns
    each frame's split and the number of passages.

    Deterministic like ``compute_split``: same frames, ratios and seed,
    same answer.
    """
    passages = group_into_passages(frames, gap_ms)
    passage_split = compute_split(sorted(set(passages.values())), train_ratio, val_ratio, test_ratio, seed)
    return {frame_id: passage_split[passage] for frame_id, passage in passages.items()}, len(passage_split)
