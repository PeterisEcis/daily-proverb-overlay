"""Plain data passed between pipeline stages.

These are deliberately dumb: no network calls, no file IO, no Pillow imports.
That makes them trivial to build by hand in tests and keeps it obvious which
fields come from an external API and therefore might be missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Attribution:
    """Credit information for one Commons file.

    Only `file_page_url` is guaranteed. Everything else is best-effort, because
    `extmetadata` is uploader-supplied: fields get skipped, and older files
    predate the structured metadata entirely. Never assume a field is present
    and never hardcode a value -- an overlay published with the wrong author or
    license is a license violation, not a cosmetic bug.
    """

    file_page_url: str
    title: str | None = None
    artist: str | None = None
    credit: str | None = None
    license_short_name: str | None = None
    license_url: str | None = None


@dataclass(frozen=True)
class PictureOfTheDay:
    """A Commons POTD, already resolved to a downloadable thumbnail."""

    potd_date: date
    file_title: str
    """Full Commons title, e.g. `File:Example.jpg`."""

    image_url: str
    """The URL we actually download -- a scaled thumbnail, not the original."""

    width: int
    height: int
    mime: str | None
    attribution: Attribution


@dataclass(frozen=True)
class Quote:
    """The text burned onto the image.

    `author` and `source` are optional so that placeholder providers (and
    anonymous proverbs) do not have to invent one.
    """

    text: str
    author: str | None = None
    source: str | None = None
    provider: str = "unknown"
    """Which QuoteProvider produced this, recorded in the output metadata."""

    @property
    def is_placeholder(self) -> bool:
        """True while we are still running on filler text."""
        return self.provider == "lorem"
