"""What a detection run will cost, before it is started.

The complaint behind this is specific: a disk with 9.4 GB free and a
90,003-frame video. A run that will not fit should be refused at the
button, not discovered at 80% with the disk full and a half-written
run to clean up.

Every number here is an estimate and says so. The two that matter are
what the run itself writes - now very little, because crops are cut on
demand - and what reviewing all of it would add, which is the number
that actually fills a disk.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from app.db.models.source import Source

#: Vehicles per frame on gantry footage. A guess, and used only to
#: size an estimate: two lanes busy is the common case, and being
#: wrong by one vehicle changes the answer by a factor of two, not an
#: order of magnitude.
ASSUMED_VEHICLES_PER_FRAME = 2.0

#: Roughly what one frame_candidates row plus its share of indexes
#: costs in SQLite. Measured loosely, not exactly: it is an estimate
#: of an estimate and only needs the right magnitude.
ROW_BYTES = 400

#: JPEG at quality 90 lands near this many bytes per pixel on real
#: traffic footage - busy scenes compress worse than empty ones.
JPEG_BYTES_PER_PIXEL = 0.12

#: Never start a run that would leave less than this free. Below it
#: Windows starts failing writes in ways that have nothing to do with
#: this app.
SPACE_FLOOR_BYTES = 1 * 1024**3

#: Checked during the run too. A run that reaches this stops cleanly
#: with what it has rather than filling the disk.
STOP_BELOW_BYTES = SPACE_FLOOR_BYTES


@dataclass
class RunEstimate:
    """What a run would process, and what it would cost."""

    frames_to_process: int
    #: Rows the run itself writes. Detections are unknowable in
    #: advance, so this is frames times an assumed vehicle count.
    rows_expected: int
    #: What the run writes while it runs.
    bytes_now: int
    #: What it would come to if every frame it keeps is later opened
    #: for labelling, since that is what decodes a full-size image.
    #: The number that actually fills a disk.
    bytes_if_every_frame_reviewed: int
    free_bytes: int
    fits: bool
    #: Why not, when it does not fit. Null when it does.
    reason: str | None = None


def frames_at(source: Source, target_fps: float | None, every_frame: bool) -> int:
    """How many frames a run would look at."""
    if source.frame_count <= 0:
        return 0
    if every_frame or not target_fps:
        return source.frame_count
    if source.fps <= 0 or target_fps >= source.fps:
        return source.frame_count
    return max(1, int(source.frame_count * target_fps / source.fps))


def free_bytes_for(path: Path) -> int:
    """Free space on the disk holding ``path``, walking up to a parent
    that exists - a workspace directory may not be there yet."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def estimate_run(
    source: Source,
    workspace_path: Path,
    target_fps: float | None = None,
    every_frame: bool = False,
) -> RunEstimate:
    """What this run would process and cost, and whether it fits."""
    frames = frames_at(source, target_fps, every_frame)
    rows = int(frames * ASSUMED_VEHICLES_PER_FRAME)
    bytes_now = rows * ROW_BYTES

    pixels = max(source.width, 1) * max(source.height, 1)
    bytes_reviewed = bytes_now + int(frames * pixels * JPEG_BYTES_PER_PIXEL)

    free = free_bytes_for(workspace_path)
    fits = bytes_now + SPACE_FLOOR_BYTES <= free
    reason = None
    if not fits:
        reason = (
            f"This run would write about {_readable(bytes_now)} and only {_readable(free)} is free. "
            f"Free some space first - the Storage tool can tell you where it went."
        )
    return RunEstimate(
        frames_to_process=frames,
        rows_expected=rows,
        bytes_now=bytes_now,
        bytes_if_every_frame_reviewed=bytes_reviewed,
        free_bytes=free,
        fits=fits,
        reason=reason,
    )


def _readable(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"
