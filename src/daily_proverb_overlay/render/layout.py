"""Text measurement, wrapping, and fitting.

`textwrap` from the standard library wraps on character counts, which is wrong
for proportional fonts -- "WWWWW" and "iiiii" are the same number of characters
and wildly different widths. Everything here measures with the actual font
metrics instead.

The other job is fitting. Quote length is not under our control, so rather than
clipping a long quote the font size is reduced until the wrapped block fits its
allotted box.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from daily_proverb_overlay.render.fonts import FontResolver

log = logging.getLogger(__name__)

MIN_FONT_SIZE = 10
FONT_SIZE_STEP_RATIO = 0.92
"""Each fitting attempt shrinks the font by this factor -- ~8% per step."""


@dataclass(frozen=True)
class TextBlock:
    """A wrapped run of text, with the measurements needed to place it."""

    lines: tuple[str, ...]
    font: ImageFont.FreeTypeFont
    line_height: int
    width: int
    height: int

    @property
    def is_empty(self) -> bool:
        return not self.lines


class TextMeasurer:
    """Measures and wraps text for one font.

    A throwaway 1x1 drawing context is used for measurement so that callers can
    plan a layout before any real canvas exists.
    """

    def __init__(self, font: ImageFont.FreeTypeFont, line_spacing: float = 1.25) -> None:
        self.font = font
        self.line_spacing = line_spacing
        self._draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    @property
    def line_height(self) -> int:
        """Baseline-to-baseline distance, including leading."""
        ascent, descent = self.font.getmetrics()
        return int((ascent + descent) * self.line_spacing)

    def text_width(self, text: str) -> int:
        return int(self._draw.textlength(text, font=self.font))

    def wrap(self, text: str, max_width: int) -> TextBlock:
        """Greedily wrap `text` so no line exceeds `max_width` pixels."""
        words = text.split()
        if not words:
            return TextBlock((), self.font, self.line_height, 0, 0)

        lines: list[str] = []
        current = ""

        for word in words:
            candidate = f"{current} {word}".strip()
            if self.text_width(candidate) <= max_width:
                current = candidate
                continue

            if current:
                lines.append(current)
            # A single word wider than the box has to be broken mid-word;
            # unbroken it would overflow the image.
            if self.text_width(word) > max_width:
                head, current = self._break_word(word, max_width)
                lines.extend(head)
            else:
                current = word

        if current:
            lines.append(current)

        line_height = self.line_height
        return TextBlock(
            lines=tuple(lines),
            font=self.font,
            line_height=line_height,
            width=max((self.text_width(line) for line in lines), default=0),
            height=line_height * len(lines),
        )

    def _break_word(self, word: str, max_width: int) -> tuple[list[str], str]:
        """Split an over-wide word into full lines plus a trailing remainder."""
        completed: list[str] = []
        chunk = ""

        for char in word:
            if self.text_width(chunk + char) <= max_width or not chunk:
                chunk += char
            else:
                completed.append(chunk)
                chunk = char

        return completed, chunk


def fit_text(
    text: str,
    *,
    resolver: FontResolver,
    max_width: int,
    max_height: int,
    start_size: int,
    line_spacing: float = 1.25,
) -> TextBlock:
    """Wrap `text` at the largest font size that fits `max_width` x `max_height`.

    Falls back to `MIN_FONT_SIZE` if even that overflows, in which case the block
    is returned oversized rather than clipped -- better a cramped overlay than a
    silently truncated quote.
    """
    size = max(start_size, MIN_FONT_SIZE)

    while size >= MIN_FONT_SIZE:
        block = TextMeasurer(resolver.load(size), line_spacing).wrap(text, max_width)
        if block.height <= max_height:
            return block
        size = int(size * FONT_SIZE_STEP_RATIO)

    log.warning("quote does not fit in %dx%d even at the minimum font size", max_width, max_height)
    return TextMeasurer(resolver.load(MIN_FONT_SIZE), line_spacing).wrap(text, max_width)
