"""An exported dataset version, as one file you can take away.

The export already writes a YOLO folder. What was missing was a way
to get it off this machine: to train on a box with a real GPU, or to
hand the data to somebody else.

Streamed, never written to disk. A second copy of the dataset is the
one thing this app cannot afford - it runs on a drive with single
digit gigabytes free - and the zip is read once and sent.
"""

import zipfile
from collections.abc import Iterator
from pathlib import Path

from app.core.errors import AppError, NotFoundError
from app.services.retraining_handoff import portable_data_yaml, portable_instructions

#: Files the archive never carries out of the workspace, whatever is
#: sitting in the export directory.
EXCLUDED_NAMES = {"data.yaml", "RETRAINING.md"}

#: Read in chunks rather than whole files: a 1080p JPEG is small, but
#: there can be thousands of them and the point is to hold none of it.
CHUNK_BYTES = 1024 * 1024


class ArchiveUnavailableError(AppError):
    code = "archive_unavailable"


def archive_name(project_name: str, version: int) -> str:
    """What the file is called once it is somewhere else.

    Named after the project and the version because a folder of
    downloads full of ``dataset.zip`` is no use to anyone.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in project_name).strip("-")
    return f"{safe or 'dataset'}-v{version}-yolo.zip"


def archive_bytes(export_dir: Path) -> int:
    """Roughly how large the download will be.

    Rough because the two generated files are not counted and zip
    keeps its own bookkeeping - but the images are all of it, and
    knowing "13 MB" before clicking is the point.
    """
    if not export_dir.is_dir():
        return 0
    return sum(path.stat().st_size for path in export_dir.rglob("*") if path.is_file())


def stream_archive(export_dir: Path, class_names: list[str], base_model: str) -> Iterator[bytes]:
    """The export directory as zip bytes, ready to send.

    Stored rather than deflated: JPEGs are already compressed, so
    deflating them spends CPU to save nothing.

    The ``data.yaml`` that goes in the archive is not the one on
    disk. That one carries this machine's absolute path, which is
    exactly what is wrong with it once the dataset is somewhere else.
    """
    if not export_dir.is_dir():
        raise NotFoundError(f"This dataset version's files are not on disk: {export_dir}")

    buffer = _Spool()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("data.yaml", portable_data_yaml(class_names))
        archive.writestr("RETRAINING.md", portable_instructions(base_model))
        yield from buffer.drain()

        for path in sorted(export_dir.rglob("*")):
            if not path.is_file() or path.name in EXCLUDED_NAMES:
                continue
            with archive.open(path.relative_to(export_dir).as_posix(), "w") as entry:
                with path.open("rb") as source:
                    while chunk := source.read(CHUNK_BYTES):
                        entry.write(chunk)
                        yield from buffer.drain()
            yield from buffer.drain()

    yield from buffer.drain()


class _Spool:
    """A file-like sink that hands back whatever has been written.

    ``ZipFile`` writes to a file object; a download wants an
    iterator. This is the join between them, and it is what keeps the
    whole dataset from being held in memory or staged on disk: each
    time the zip writes, the bytes are handed straight to the
    response and dropped.
    """

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._position = 0

    def write(self, data: bytes) -> int:
        self._parts.append(bytes(data))
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:  # pragma: no cover - required by ZipFile
        return None

    def drain(self) -> Iterator[bytes]:
        parts, self._parts = self._parts, []
        for part in parts:
            if part:
                yield part
