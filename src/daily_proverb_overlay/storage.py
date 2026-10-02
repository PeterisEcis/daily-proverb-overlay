"""Where a day's output lands, and how the job avoids redoing work.

Three rules make the daily job safe to rerun:

1. Everything for a date lives in one directory named after that date, so a
   rerun has an obvious place to look and nothing can collide.
2. `is_complete()` is the skip check. It requires *every* expected file, so a
   partial run is treated as unfinished rather than done.
3. Writes go to a temp file in the destination directory and are then
   `os.replace()`d into place. A crash mid-write leaves the temp file behind,
   never a half-written file that rule 2 would mistake for finished.

Rule 3 is the one that matters once this runs unattended on a schedule: a job
killed halfway through must not poison every subsequent run.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

SOURCE_IMAGE_STEM = "source"
FINAL_IMAGE_NAME = "overlay.jpg"
ATTRIBUTION_NAME = "attribution.txt"
METADATA_NAME = "metadata.json"


@dataclass(frozen=True)
class OutputPaths:
    """Every file one run produces."""

    directory: Path
    source_image: Path
    final_image: Path
    attribution: Path
    metadata: Path

    @property
    def published_files(self) -> tuple[Path, ...]:
        """The files whose presence means the run finished.

        The downloaded source is excluded on purpose: it is a cache, and
        deleting it should not force a re-render.
        """
        return (self.final_image, self.attribution, self.metadata)


class OutputStore:
    """Resolves output paths and writes them atomically."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def paths_for(self, output_date: date, source_suffix: str = ".jpg") -> OutputPaths:
        directory = self.root / output_date.isoformat()
        return OutputPaths(
            directory=directory,
            source_image=directory / f"{SOURCE_IMAGE_STEM}{source_suffix}",
            final_image=directory / FINAL_IMAGE_NAME,
            attribution=directory / ATTRIBUTION_NAME,
            metadata=directory / METADATA_NAME,
        )

    def is_complete(self, paths: OutputPaths) -> bool:
        """True when every published file exists and is non-empty."""
        return all(path.is_file() and path.stat().st_size > 0 for path in paths.published_files)

    def ensure_directory(self, paths: OutputPaths) -> None:
        paths.directory.mkdir(parents=True, exist_ok=True)

    def write_text(self, path: Path, text: str) -> None:
        """Write UTF-8 text atomically."""
        self._atomic_write(path, text.encode("utf-8"))

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        """Write pretty-printed JSON atomically."""
        encoded = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        self._atomic_write(path, f"{encoded}\n".encode())

    def write_image(self, path: Path, image: Image.Image, **save_options: Any) -> None:
        """Save a Pillow image atomically.

        Pillow picks its format from the file extension, so the temp file keeps
        the destination's suffix.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._temp_path(path)
        try:
            image.save(temp_path, **save_options)
            os.replace(temp_path, path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        log.debug("wrote %s", path)

    def clean_partials(self, paths: OutputPaths) -> int:
        """Delete leftover temp files from interrupted runs. Returns the count."""
        if not paths.directory.is_dir():
            return 0
        removed = 0
        # Matches both shapes of temp file: `.name.xxxx.part` from a download and
        # `.stem.xxxx.part.jpg` from an image save.
        for stale in paths.directory.glob(".*.part*"):
            stale.unlink(missing_ok=True)
            removed += 1
        if removed:
            log.info("removed %d leftover partial file(s) in %s", removed, paths.directory)
        return removed

    def _atomic_write(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._temp_path(path)
        try:
            temp_path.write_bytes(payload)
            os.replace(temp_path, path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        log.debug("wrote %s", path)

    @staticmethod
    def _temp_path(path: Path) -> Path:
        """Reserve a temp path beside `path`, so the rename never crosses a volume.

        The suffix keeps the destination's extension last, because Pillow picks
        its output format from the extension.
        """
        descriptor, name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.stem}.", suffix=f".part{path.suffix}"
        )
        os.close(descriptor)
        return Path(name)
