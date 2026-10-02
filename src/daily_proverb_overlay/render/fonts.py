"""Finding a real TrueType font file.

Pillow ships a default font, but it is a fixed-size bitmap: it cannot scale, so
it is useless for an overlay whose text size follows the image size. This module
therefore insists on a real `.ttf`/`.otf` and fails loudly when there is none.

Expect this to be the first thing that breaks inside a container.
`python:*-slim` images contain no fonts at all, so a resolver that works on a
developer laptop finds nothing in the image. Two fixes, both worth trying:

    # install fonts in the image
    RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core

    # or bundle one and point at it
    ENV POTD_FONT=/app/src/daily_proverb_overlay/assets/fonts/YourFont.ttf

Bundling is the more reproducible of the two -- the image then renders the same
text regardless of what the base image happens to ship -- but only if the font's
license permits redistribution. The OFL fonts (DejaVu, Noto, Inter) do.
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

from PIL import ImageFont

log = logging.getLogger(__name__)

BUNDLED_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
"""Fonts committed to the repo, searched before anything system-provided."""

FONT_SUFFIXES = (".ttf", ".otf", ".ttc")

SYSTEM_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "Windows": (
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\verdana.ttf",
    ),
    "Linux": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ),
    "Darwin": (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ),
}


class FontNotFoundError(RuntimeError):
    """No usable TrueType font could be located."""


class FontResolver:
    """Locates one font file and loads it at whatever size the layout asks for.

    Search order, first hit wins:

    1. An explicit path (`--font` / `POTD_FONT`)
    2. Any font bundled in `assets/fonts/`
    3. Known system font paths for the current platform
    """

    def __init__(self, explicit_path: Path | None = None) -> None:
        self.explicit_path = explicit_path
        self._resolved: Path | None = None

    def resolve(self) -> Path:
        """Return the font file to use, searching only once per instance."""
        if self._resolved is None:
            self._resolved = self._search()
            log.debug("using font %s", self._resolved)
        return self._resolved

    def load(self, size: int) -> ImageFont.FreeTypeFont:
        """Load the resolved font at `size` pixels."""
        path = self.resolve()
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError as exc:
            raise FontNotFoundError(f"{path} could not be loaded as a font: {exc}") from exc

    def _search(self) -> Path:
        if self.explicit_path is not None:
            if self.explicit_path.is_file():
                return self.explicit_path
            raise FontNotFoundError(
                f"configured font {self.explicit_path} does not exist. Check --font / POTD_FONT."
            )

        bundled = self._first_bundled_font()
        if bundled is not None:
            return bundled

        for candidate in SYSTEM_FONT_CANDIDATES.get(platform.system(), ()):
            path = Path(candidate)
            if path.is_file():
                return path

        raise FontNotFoundError(self._not_found_message())

    def _first_bundled_font(self) -> Path | None:
        if not BUNDLED_FONT_DIR.is_dir():
            return None
        fonts = sorted(
            path for path in BUNDLED_FONT_DIR.iterdir() if path.suffix.lower() in FONT_SUFFIXES
        )
        return fonts[0] if fonts else None

    def _not_found_message(self) -> str:
        searched = [str(BUNDLED_FONT_DIR), *SYSTEM_FONT_CANDIDATES.get(platform.system(), ())]
        locations = "\n  ".join(searched)
        return (
            "No TrueType font found. Pillow's built-in font is a fixed-size bitmap "
            "and cannot be scaled, so text cannot be rendered without one.\n"
            f"Searched:\n  {locations}\n"
            "Fix by dropping a .ttf into assets/fonts/, or by setting POTD_FONT "
            "to a font file. In a slim container image, install fonts-dejavu-core."
        )
