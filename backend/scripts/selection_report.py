"""Report what frame selection would do to a source, and why.

The thresholds in ``frame_selection`` were chosen from this, not from
taste. Run it against a source to see whether they still suit your
footage:

    python scripts/selection_report.py <source-id>

Decoding dominates - a few thousand 1080p frames take minutes - so the
signals are cached beside the database and reused on later runs.
"""

import argparse
import json
import time
from pathlib import Path

from sqlalchemy import select

from app.db.models.frame import Frame
from app.db.models.frame_candidate import FrameCandidate
from app.db.models.source import Source
from app.db.session import SessionLocal
from app.services.frame_selection import (
    DEFAULT_SELECTION_CONFIG,
    FrameSignals,
    SelectionConfig,
    brightness_of,
    frame_quality,
    hamming_distance,
    perceptual_hash,
    select_frames,
)
from app.services.jobs.handlers import _decode

SWEEP = (0, 1, 2, 3, 4, 6, 8, 12)


def gather(source_id: str, cache: Path) -> list[FrameSignals]:
    if cache.is_file():
        return [FrameSignals(**row) for row in json.loads(cache.read_text(encoding="utf-8"))]

    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None:
            raise SystemExit(f"No such source: {source_id}")
        frames = list(db.scalars(select(Frame).where(Frame.source_id == source_id).order_by(Frame.frame_index)))
        if not frames:
            raise SystemExit("That source has no frames - run detection first.")

        candidates: dict[str, list] = {}
        for candidate in db.scalars(
            select(FrameCandidate).join(Frame, FrameCandidate.frame_id == Frame.id).where(Frame.source_id == source_id)
        ):
            candidates.setdefault(candidate.frame_id, []).append(candidate)

        print(f"decoding {len(frames)} frames...", flush=True)
        started = time.time()
        rows = []
        for done, (frame, image) in enumerate(_decode(frames, source), start=1):
            own = candidates.get(frame.id, [])
            rows.append(
                {
                    "frame_id": frame.id,
                    "frame_index": frame.frame_index,
                    "quality": frame_quality(own),
                    "vehicle_count": len(own),
                    "brightness": brightness_of(image),
                    "phash": perceptual_hash(image),
                }
            )
            if done % 500 == 0:
                print(f"  {done}/{len(frames)} ({time.time() - started:.0f}s)", flush=True)
        cache.write_text(json.dumps(rows), encoding="utf-8")
        print(f"decoded in {time.time() - started:.0f}s")
        return [FrameSignals(**row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_id")
    parser.add_argument("--cache", type=Path, default=Path("data/selection-signals.json"))
    args = parser.parse_args()

    signals = gather(args.source_id, args.cache)

    print()
    print(f"frames            {len(signals)}")
    print(f"quality           {min(s.quality for s in signals):.3f} - {max(s.quality for s in signals):.3f}")
    print(f"vehicles          {min(s.vehicle_count for s in signals)} - {max(s.vehicle_count for s in signals)}")
    print(f"brightness        {min(s.brightness for s in signals):.3f} - {max(s.brightness for s in signals):.3f}")
    print(f"no detections     {sum(1 for s in signals if s.vehicle_count == 0)}")

    print()
    print("How close are frames to each other?")
    consecutive = sorted(hamming_distance(signals[i].phash, signals[i + 1].phash) for i in range(len(signals) - 1))
    step = max(1, len(signals) // 60)
    apart = sorted(
        hamming_distance(signals[i].phash, signals[j].phash)
        for i in range(0, len(signals), step)
        for j in range(0, len(signals), step)
        if i < j
    )
    for name, values in (("consecutive frames", consecutive), ("any two frames", apart)):
        if not values:
            continue
        print(
            f"  {name:20} median={values[len(values) // 2]:3} "
            f"p10={values[len(values) // 10]:3} p90={values[9 * len(values) // 10]:3}"
        )
    print()
    print("  A useful threshold sits above the consecutive p90 and below the any-two p10.")
    print(f"  The default is {DEFAULT_SELECTION_CONFIG.hamming_threshold}.")

    print()
    print(f"{'threshold':>10} {'offered':>8} {'% of run':>9}")
    for threshold in SWEEP:
        kept = sum(1 for d in select_frames(signals, SelectionConfig(hamming_threshold=threshold)) if d.selected)
        marker = "  <- default" if threshold == DEFAULT_SELECTION_CONFIG.hamming_threshold else ""
        print(f"{threshold:>10} {kept:>8} {100 * kept / len(signals):>8.1f}%{marker}")


if __name__ == "__main__":
    main()
