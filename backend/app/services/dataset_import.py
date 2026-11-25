"""Bringing in a YOLO dataset someone else already put boxes on.

A production node exports what its camera saw as a YOLO dataset -
``images/``, ``labels/``, ``data.yaml``, and a ``meta.jsonl`` with each
box's score and when the picture was taken. Those boxes are the
production model's own guesses. They are worth having - most are right,
and correcting a right box is one keypress - but they are not labels
until a person has looked.

So an import becomes one source whose frames wait in the labelling
queue, each carrying the model's boxes as ``model`` annotations. The
canvas already treats those as pre-annotations: saving a box makes it
the human's, leaving it out deletes it, and a frame thrown out is
``rejected`` and never exported. Nothing here decides a box is wrong.

What it does decide is the order. The boxes most likely to be wrong
come first:

* ``repeated_spot`` - a box in the same place in many pictures. A plate
  moves with its vehicle; a road stud does not. At a barrier, stopped
  vehicles can put real plates in one place too, which is why this is a
  flag for a person and never a deletion.
* ``low_confidence`` - the model itself was unsure.

Images already imported into the project (same file name) are skipped,
so this week's export can be brought in on top of last week's.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

import cv2
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models.annotation import Annotation
from app.db.models.class_definition import ClassDefinition
from app.db.models.frame import Frame
from app.db.models.project import Project
from app.db.models.source import Source
from app.services.class_definitions import list_project_classes
from app.services.frame_materializer import frames_root

SOURCE_TYPE = "dataset"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
#: Everything else a YOLO dataset carries that this reads. Anything
#: outside these is left in the archive.
SIDE_FILE_SUFFIXES = {".txt", ".yaml", ".yml", ".jsonl"}

#: Refuse an archive that unpacks to more than this - a zip bomb, or a
#: whole drive zipped by mistake. A week of one camera is ~100 MB.
MAX_UNPACKED_BYTES = 8 * 1024**3

REASON_REPEATED_SPOT = "repeated_spot"
REASON_LOW_CONFIDENCE = "low_confidence"

#: Below this the model was guessing. Matches the review threshold
#: people used by eye on the first production exports.
LOW_CONFIDENCE = 0.4
#: A box counts as repeated when this many pictures have one in the
#: same place, of about the same size.
REPEAT_MIN_IMAGES = 5
#: "Same place": centres within this fraction of the frame.
REPEAT_CENTRE_TOLERANCE = 0.01
#: "About the same size": width and height within this ratio.
REPEAT_SIZE_RATIO = 1.5

#: Matching a ``labels/`` line to its ``meta.jsonl`` entry: the export
#: writes both from one box, so they agree to the rounding of either.
_META_MATCH_TOLERANCE = 2e-3


class DatasetImportError(AppError):
    code = "dataset_import_failed"


@dataclass
class _Box:
    yolo_class: int
    cx: float
    cy: float
    w: float
    h: float
    confidence: float | None = None
    repeated: bool = False


@dataclass
class _Image:
    path: Path
    name: str
    boxes: list[_Box]
    captured_at: datetime | None
    width: int = 0
    height: int = 0


@dataclass
class DatasetImportResult:
    source: Source
    images_imported: int
    boxes_imported: int
    #: Already in the project from an earlier import, so left out.
    already_imported: int
    #: Files named like images that do not open as one.
    unreadable: int
    repeated_spot_frames: int
    low_confidence_frames: int
    #: Dataset class name -> the project class its boxes were put in.
    class_mapping: dict[str, str]


def import_from_path(db: Session, project: Project, path: Path) -> DatasetImportResult:
    """Import a dataset sitting on the server: a ``.zip`` or the folder
    one unpacks to."""
    if path.is_dir():
        return import_directory(db, project, path, display_name=path.name)
    if path.is_file():
        return import_archive(db, project, path, display_name=path.name)
    raise NotFoundError(f"Dataset not found: {path}", code="dataset_not_found")


def import_archive(db: Session, project: Project, archive: Path, display_name: str) -> DatasetImportResult:
    """Unpack a dataset ``.zip`` next to the project and import it.

    Unpacked inside the workspace so moving the images into place is a
    rename on the same disk, and removed afterwards either way.
    """
    if not zipfile.is_zipfile(archive):
        raise DatasetImportError(
            f"{display_name} is not a .zip file. Export the dataset as a zip, or import the folder it unpacks to.",
            code="dataset_not_a_zip",
        )
    workspace = Path(project.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dataset-import-", dir=workspace) as unpacked:
        _safe_extract(archive, Path(unpacked))
        return import_directory(db, project, Path(unpacked), display_name=display_name)


def import_directory(db: Session, project: Project, root: Path, display_name: str) -> DatasetImportResult:
    """Import the YOLO dataset under ``root`` as one new source.

    Adds rows to ``db`` without committing; the caller commits. The
    images are copied into the workspace first and removed again if
    anything after that fails, so a failed import leaves nothing behind.
    """
    names, images = _read_dataset(root)
    if not images:
        raise DatasetImportError(
            f"No images found in {display_name}. Expected a YOLO dataset: images/ beside labels/.",
            code="dataset_empty",
        )

    class_map = _map_classes(list_project_classes(db, project.id), names, images)

    known = _already_imported_names(db, project.id)
    fresh = [image for image in images if image.name not in known]
    already_imported = len(images) - len(fresh)

    unreadable = 0
    readable: list[_Image] = []
    for image in fresh:
        # A full decode, not a header read: a truncated upload has a
        # perfectly good header, and would fail later on the canvas.
        pixels = cv2.imread(str(image.path))
        if pixels is None:
            unreadable += 1
            continue
        image.height, image.width = pixels.shape[:2]
        readable.append(image)

    if not readable:
        if already_imported:
            raise ConflictError(
                f"Every image in {display_name} is already in this project ({already_imported}). Nothing new to import.",
                code="dataset_already_imported",
            )
        raise DatasetImportError(f"None of the images in {display_name} could be opened.", code="dataset_unreadable")

    _flag_repeated_spots(readable)
    ordered = sorted(readable, key=_review_priority)
    timestamps = _timestamps_ms(ordered)

    size = Counter((image.width, image.height) for image in ordered).most_common(1)[0][0]
    source = Source(
        id=str(uuid.uuid4()),
        project_id=project.id,
        type=SOURCE_TYPE,
        path_or_uri=display_name,
        fps=0.0,
        width=size[0],
        height=size[1],
        duration_ms=max(timestamps.values(), default=0),
        frame_count=len(ordered),
    )
    db.add(source)

    destination = frames_root(Path(project.workspace_path)) / source.id
    destination.mkdir(parents=True, exist_ok=True)
    boxes_imported = repeated_frames = low_frames = 0
    try:
        for frame_index, image in enumerate(ordered):
            stored = destination / image.name
            shutil.copy2(image.path, stored)
            reason = _reason(image)
            repeated_frames += reason == REASON_REPEATED_SPOT
            low_frames += reason == REASON_LOW_CONFIDENCE
            frame = Frame(
                id=str(uuid.uuid4()),
                source_id=source.id,
                frame_index=frame_index,
                timestamp_ms=timestamps[image.name],
                width=image.width,
                height=image.height,
                image_path=str(stored),
                status="pending",
                selection_reason=reason,
            )
            db.add(frame)
            for box in image.boxes:
                bbox = _pixel_box(box, image.width, image.height)
                if bbox is None:
                    continue
                db.add(
                    Annotation(
                        frame_id=frame.id,
                        frame_candidate_id=None,
                        source="model",
                        class_id=class_map[box.yolo_class],
                        bbox_json=bbox,
                        attributes={},
                        confidence=box.confidence,
                        status="pending",
                    )
                )
                boxes_imported += 1
        db.flush()
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

    project_class_names = {c.class_id: c.name for c in list_project_classes(db, project.id)}
    return DatasetImportResult(
        source=source,
        images_imported=len(ordered),
        boxes_imported=boxes_imported,
        already_imported=already_imported,
        unreadable=unreadable,
        repeated_spot_frames=repeated_frames,
        low_confidence_frames=low_frames,
        class_mapping={
            names.get(index, str(index)): project_class_names[class_id] for index, class_id in class_map.items()
        },
    )


# -- reading the dataset -----------------------------------------------------


def _safe_extract(archive: Path, destination: Path) -> None:
    """Unpack only what a dataset needs, and only inside ``destination``.

    A zip names its own paths, so an entry of ``../../app.db`` or
    ``/etc/passwd`` is refused rather than followed.
    """
    with zipfile.ZipFile(archive) as zf:
        members = []
        total = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            relative = PurePosixPath(info.filename.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts or _is_junk(relative.parts):
                continue
            if relative.suffix.lower() not in IMAGE_SUFFIXES | SIDE_FILE_SUFFIXES:
                continue
            total += info.file_size
            members.append((info, relative))
        if total > MAX_UNPACKED_BYTES:
            raise DatasetImportError(
                f"{archive.name} unpacks to {total / 1024**3:.1f} GB, more than the "
                f"{MAX_UNPACKED_BYTES / 1024**3:.0f} GB an import takes. Split it.",
                code="dataset_too_large",
            )
        for info, relative in members:
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _read_dataset(root: Path) -> tuple[dict[int, str], list[_Image]]:
    names = _class_names(root)
    meta = _read_meta(root)

    images: list[_Image] = []
    seen: set[str] = set()
    candidates = (
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and not _is_junk(p.relative_to(root).parts)
    )
    for path in sorted(candidates):
        # The file name is what identifies a picture across imports, so
        # two with one name in one archive are one picture twice.
        if path.name in seen:
            continue
        seen.add(path.name)
        entry = meta.get(path.name, {})
        meta_boxes = entry.get("boxes", [])
        label = _label_path(path)
        boxes = _read_label(label) if label.is_file() else [_Box(*b) for b in meta_boxes]
        if label.is_file():
            _attach_confidence(boxes, meta_boxes)
        images.append(_Image(path=path, name=path.name, boxes=boxes, captured_at=entry.get("captured_at")))
    return names, images


def _is_junk(parts: tuple[str, ...]) -> bool:
    """What a Mac adds to a zip it makes: ``__MACOSX/`` and ``._name``
    files, named like the images but holding none."""
    return "__MACOSX" in parts or parts[-1].startswith("._")


def _class_names(root: Path) -> dict[int, str]:
    """``names`` from the dataset's yaml, shallowest file first. A YOLO
    yaml writes it as a list or as an index -> name mapping."""
    for candidate in sorted(root.rglob("*.y*ml"), key=lambda p: len(p.parts)):
        try:
            data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError):
            continue
        raw = data.get("names") if isinstance(data, dict) else None
        if isinstance(raw, list):
            return {i: str(name) for i, name in enumerate(raw)}
        if isinstance(raw, dict):
            return {int(i): str(name) for i, name in raw.items()}
    return {}


def _read_meta(root: Path) -> dict[str, dict]:
    """``meta.jsonl``, by image file name: when it was taken, and each
    box as ``(class, cx, cy, w, h, confidence)``."""
    found = sorted(root.rglob("meta.jsonl"), key=lambda p: len(p.parts))
    if not found:
        return {}
    meta: dict[str, dict] = {}
    for line in found[0].read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not row.get("image"):
            continue
        boxes = []
        for raw in row.get("boxes") or []:
            # [class, confidence, cx, cy, w, h]
            try:
                cls, conf, cx, cy, w, h = raw
                boxes.append((int(cls), float(cx), float(cy), float(w), float(h), float(conf)))
            except (TypeError, ValueError):
                continue
        meta[PurePosixPath(str(row["image"])).name] = {
            "boxes": boxes,
            "captured_at": _parse_time(row.get("capturedAt")),
        }
    return meta


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _label_path(image: Path) -> Path:
    """Where YOLO keeps an image's boxes: the same path with the last
    ``images`` folder swapped for ``labels``, else beside the image."""
    parts = list(image.parts)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "images":
            parts[i] = "labels"
            return Path(*parts).with_suffix(".txt")
    return image.with_suffix(".txt")


def _read_label(path: Path) -> list[_Box]:
    """``class cx cy w h`` per line, normalised; a sixth number, where an
    exporter saved it, is the score. Lines that are not that are skipped."""
    boxes = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            cls = int(float(parts[0]))
            cx, cy, w, h = (float(v) for v in parts[1:5])
            conf = float(parts[5]) if len(parts) > 5 else None
        except ValueError:
            continue
        boxes.append(_Box(cls, cx, cy, w, h, conf))
    return boxes


def _attach_confidence(boxes: list[_Box], meta_boxes: list[tuple]) -> None:
    for box in boxes:
        if box.confidence is not None:
            continue
        for cls, cx, cy, w, h, conf in meta_boxes:
            if cls == box.yolo_class and max(
                abs(cx - box.cx), abs(cy - box.cy), abs(w - box.w), abs(h - box.h)
            ) <= _META_MATCH_TOLERANCE:
                box.confidence = conf
                break


# -- deciding ----------------------------------------------------------------


def _map_classes(
    project_classes: list[ClassDefinition], names: dict[int, str], images: list[_Image]
) -> dict[int, int]:
    """Which project class each dataset class goes into.

    By name first. Failing that: a project with one class takes
    everything, and a one-class dataset goes to the project's plate class
    - a production plate model often calls its only class "0" or "1".
    Anything still unplaced stops the import rather than landing in the
    wrong class.
    """
    used = sorted({box.yolo_class for image in images for box in image.boxes})
    if not used:
        return {}
    if not project_classes:
        raise ConflictError(
            "This project has no classes yet. Add one (Classes) before importing a dataset.",
            code="class_mapping_needed",
        )

    by_name = {c.name.strip().lower(): c for c in project_classes}
    plates = [c for c in project_classes if "plate" in c.name.lower()]
    mapping: dict[int, int] = {}
    unplaced: list[str] = []
    for index in used:
        name = names.get(index, str(index))
        match = by_name.get(name.strip().lower())
        if match is None and len(project_classes) == 1:
            match = project_classes[0]
        if match is None and len(used) == 1 and len(plates) == 1:
            match = plates[0]
        if match is None:
            unplaced.append(f"{index} ({name!r})")
        else:
            mapping[index] = match.class_id
    if unplaced:
        raise ConflictError(
            f"Could not tell which class to put dataset class {', '.join(unplaced)} in. "
            f"This project has: {', '.join(c.name for c in project_classes)}. "
            "Rename a project class to match, or import into a project with only the class you want.",
            code="class_mapping_needed",
        )
    return mapping


def _already_imported_names(db: Session, project_id: str) -> set[str]:
    paths = db.scalars(
        select(Frame.image_path)
        .join(Source, Frame.source_id == Source.id)
        .where(Source.project_id == project_id, Source.type == SOURCE_TYPE, Frame.image_path.is_not(None))
    )
    return {Path(p).name for p in paths}


def _flag_repeated_spots(images: list[_Image]) -> None:
    """Mark every box that sits where boxes sit in many other pictures.

    Bucketed by position so this stays linear in the number of boxes:
    only boxes in neighbouring buckets can be within the tolerance.
    """
    cell = REPEAT_CENTRE_TOLERANCE
    buckets: dict[tuple[int, int], list[tuple[int, _Box]]] = {}
    for owner, image in enumerate(images):
        for box in image.boxes:
            buckets.setdefault((int(box.cx / cell), int(box.cy / cell)), []).append((owner, box))

    for owner, image in enumerate(images):
        for box in image.boxes:
            bx, by = int(box.cx / cell), int(box.cy / cell)
            pictures = {owner}
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other_owner, other in buckets.get((bx + dx, by + dy), []):
                        if other_owner in pictures:
                            continue
                        if (
                            abs(other.cx - box.cx) <= cell
                            and abs(other.cy - box.cy) <= cell
                            and _similar(other.w, box.w)
                            and _similar(other.h, box.h)
                        ):
                            pictures.add(other_owner)
            box.repeated = len(pictures) >= REPEAT_MIN_IMAGES


def _similar(a: float, b: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    return max(a, b) / min(a, b) <= REPEAT_SIZE_RATIO


def _reason(image: _Image) -> str | None:
    if any(box.repeated for box in image.boxes):
        return REASON_REPEATED_SPOT
    if any(box.confidence is not None and box.confidence < LOW_CONFIDENCE for box in image.boxes):
        return REASON_LOW_CONFIDENCE
    return None


def _review_priority(image: _Image) -> tuple:
    """Most-likely-wrong first: repeated spots, then the least sure, then
    the rest in the order they were taken."""
    reason = _reason(image)
    rank = 0 if reason == REASON_REPEATED_SPOT else 1 if reason == REASON_LOW_CONFIDENCE else 2
    scores = [box.confidence for box in image.boxes if box.confidence is not None]
    taken = image.captured_at.timestamp() if image.captured_at else 0.0
    return (rank, min(scores, default=1.0), taken, image.name)


def _timestamps_ms(images: list[_Image]) -> dict[str, int]:
    """Milliseconds since the earliest picture. Export groups frames into
    passages by this, so two pictures of one vehicle stay in one split.
    Without capture times, pictures are taken to be a minute apart -
    separate passages, which is the safe assumption for sampled frames.
    """
    times = [image.captured_at for image in images if image.captured_at is not None]
    if len(times) == len(images) and times:
        start = min(times)
        return {image.name: int((image.captured_at - start).total_seconds() * 1000) for image in images}
    by_name = sorted(image.name for image in images)
    return {name: i * 60_000 for i, name in enumerate(by_name)}


def _pixel_box(box: _Box, width: int, height: int) -> list[float] | None:
    """Normalised centre box -> ``[x1, y1, x2, y2]`` pixels, clipped to the
    frame. A box with no area left is dropped: the canvas would refuse it."""
    x1 = max(0.0, (box.cx - box.w / 2) * width)
    y1 = max(0.0, (box.cy - box.h / 2) * height)
    x2 = min(float(width), (box.cx + box.w / 2) * width)
    y2 = min(float(height), (box.cy + box.h / 2) * height)
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)]
