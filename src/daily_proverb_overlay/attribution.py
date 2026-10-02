"""Turning Commons metadata into the credit text the license requires.

Two renderings are needed, because they have different budgets:

* `credit_line()` is burned into the image. It has to stay on one short line, so
  it carries the minimum that still identifies the work: title, author, license.
* `full_text()` is the sidecar file, and the text to paste wherever the image
  gets published. It carries everything, including the ShareAlike consequence.

Most POTDs are CC BY-SA. An overlay is a derivative work, so ShareAlike
propagates to whatever we publish, and `full_text()` says so explicitly when the
license name indicates SA.
"""

from __future__ import annotations

from daily_proverb_overlay.models import Attribution

UNKNOWN_AUTHOR = "Unknown author"
UNKNOWN_TITLE = "Untitled"
UNKNOWN_LICENSE = "See source page for license"

CREDIT_LINE_MAX_CHARS = 110
"""Burned-in credit has to fit the image width at a small font size."""

SHARE_ALIKE_NOTICE = (
    "This image is a derivative work: a quote has been composited onto the "
    "original. The source is licensed under a ShareAlike license, so this "
    "derivative is published under that same license."
)

DERIVATIVE_NOTICE = (
    "This image is a derivative work: a quote has been composited onto the original."
)

SHARE_ALIKE_MARKERS = ("BY-SA", "GFDL", "GPL", "FAL")
"""Short-name fragments that indicate a copyleft license."""


class AttributionFormatter:
    """Renders one `Attribution` into the strings the pipeline publishes."""

    def __init__(self, attribution: Attribution) -> None:
        self.attribution = attribution

    @property
    def title(self) -> str:
        return self.attribution.title or UNKNOWN_TITLE

    @property
    def artist(self) -> str:
        return self.attribution.artist or UNKNOWN_AUTHOR

    @property
    def license_name(self) -> str:
        return self.attribution.license_short_name or UNKNOWN_LICENSE

    @property
    def is_share_alike(self) -> bool:
        """Whether the source license propagates to derivatives.

        Matches on the short name rather than the URL, because the short name is
        the field Commons populates most reliably.
        """
        name = (self.attribution.license_short_name or "").upper()
        return any(marker in name for marker in SHARE_ALIKE_MARKERS)

    def credit_line(self) -> str:
        """The single line burned into the image."""
        line = f"{self.title} · {self.artist} · {self.license_name}"
        if len(line) <= CREDIT_LINE_MAX_CHARS:
            return line

        # Trim the title rather than the author or license: those two are the
        # parts the license actually obliges us to carry.
        fixed = f" · {self.artist} · {self.license_name}"
        room = max(CREDIT_LINE_MAX_CHARS - len(fixed), 12)
        return f"{_ellipsize(self.title, room)}{fixed}"

    def full_text(self) -> str:
        """The complete attribution, for the sidecar file and publish captions."""
        lines = [
            f"Title:   {self.title}",
            f"Author:  {self.artist}",
            f"Source:  {self.attribution.file_page_url}",
            f"License: {self._license_with_url()}",
        ]
        if self.attribution.credit:
            lines.append(f"Credit:  {self.attribution.credit}")

        lines.append("")
        lines.append(SHARE_ALIKE_NOTICE if self.is_share_alike else DERIVATIVE_NOTICE)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        """Machine-readable form, recorded alongside the image."""
        return {
            "title": self.attribution.title,
            "artist": self.attribution.artist,
            "credit": self.attribution.credit,
            "license_short_name": self.attribution.license_short_name,
            "license_url": self.attribution.license_url,
            "file_page_url": self.attribution.file_page_url,
            "share_alike": self.is_share_alike,
            "credit_line": self.credit_line(),
        }

    def _license_with_url(self) -> str:
        if self.attribution.license_url:
            return f"{self.license_name} ({self.attribution.license_url})"
        return self.license_name


def _ellipsize(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"
