"""Compositing the quote and credit line onto the image.

Everything is sized in ratios of the image, never in absolute pixels, so the
same style produces a sensible result on a 1600px thumbnail and on a 4000px
original without retuning.

Legibility over an arbitrary photograph is the whole problem here. POTDs are
not chosen to be text backdrops -- the bottom of the frame may be white snow or
black shadow -- so the text sits on a gradient scrim and carries a soft shadow.
Between them the text stays readable without hiding much of the picture.

Drawing happens on a separate RGBA layer that is composited over the base in one
step, which keeps the scrim's translucency exact rather than accumulating
rounding error from drawing semi-transparent shapes directly onto the photo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from daily_proverb_overlay.models import Quote
from daily_proverb_overlay.render.fonts import FontResolver
from daily_proverb_overlay.render.layout import TextBlock, TextMeasurer, fit_text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverlayStyle:
    """Tunable appearance. Every size is a fraction of the image's dimensions."""

    margin_ratio: float = 0.055
    """Padding from the image edge, as a fraction of image width."""

    quote_size_ratio: float = 0.052
    """Starting quote font size, as a fraction of image height."""

    author_size_ratio: float = 0.030
    credit_size_ratio: float = 0.018

    max_quote_height_ratio: float = 0.40
    """Ceiling on how much of the image the quote block may occupy."""

    line_spacing: float = 1.3
    align: str = "left"
    """`left` or `center`."""

    text_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    author_color: tuple[int, int, int, int] = (255, 255, 255, 215)
    credit_color: tuple[int, int, int, int] = (255, 255, 255, 180)

    shadow_color: tuple[int, int, int, int] = (0, 0, 0, 160)
    shadow_offset_ratio: float = 0.0025
    """Shadow offset as a fraction of image height; scales with the text."""

    scrim_color: tuple[int, int, int] = (0, 0, 0)
    scrim_opacity: float = 0.66
    """Alpha of the scrim everywhere the text actually sits."""

    scrim_fade_ratio: float = 0.20
    """Height of the fade-in band above the text, as a fraction of image height.

    The scrim is a ramp *then a plateau*, not a ramp all the way down. A pure
    gradient is weakest exactly where the first line of the quote starts, which
    is the one place legibility cannot be negotiable -- the top line ends up
    sitting on raw photo. Fading in above the text instead means every line gets
    the same full-strength backing, and the blend into the picture happens where
    there is nothing to read.
    """

    scrim_easing: float = 1.5
    """Curve of the fade-in band. >1 keeps its top edge subtle."""

    wrap_in_quote_marks: bool = True
    jpeg_quality: int = 90

    def __post_init__(self) -> None:
        if self.align not in {"left", "center"}:
            raise ValueError(f"align must be 'left' or 'center', got {self.align!r}")
        if not 0.0 <= self.scrim_opacity <= 1.0:
            raise ValueError("scrim_opacity must be between 0 and 1")


@dataclass
class RenderedOverlay:
    """The composed image, plus what was drawn on it."""

    image: Image.Image
    quote_lines: tuple[str, ...]
    credit_line: str
    font_path: Path
    quote_font_size: int = 0

    @property
    def size(self) -> tuple[int, int]:
        """Actual output dimensions.

        Worth recording separately from the API's reported thumbnail size: those
        two disagree in practice, because Commons rounds a requested width up to
        the nearest cached bucket and serves that instead.
        """
        return self.image.size


class OverlayCompositor:
    """Draws a quote and a credit line onto a photograph."""

    def __init__(self, font_resolver: FontResolver, style: OverlayStyle | None = None) -> None:
        self.fonts = font_resolver
        self.style = style or OverlayStyle()

    def render(self, image_path: Path, quote: Quote, credit_line: str) -> RenderedOverlay:
        """Composite `quote` and `credit_line` onto the image at `image_path`."""
        base = self._open_normalised(image_path)
        width, height = base.size
        style = self.style

        margin = max(int(width * style.margin_ratio), 8)
        content_width = max(width - 2 * margin, 1)

        quote_block = fit_text(
            self._quote_text(quote),
            resolver=self.fonts,
            max_width=content_width,
            max_height=int(height * style.max_quote_height_ratio),
            start_size=max(int(height * style.quote_size_ratio), 12),
            line_spacing=style.line_spacing,
        )
        author_block = self._single_line_block(
            self._byline_text(quote), content_width, style.author_size_ratio, height
        )
        credit_block = self._single_line_block(
            credit_line, content_width, style.credit_size_ratio, height
        )

        gap = max(int(height * 0.018), 4)
        candidates = (quote_block, author_block, credit_block)
        blocks = [block for block in candidates if not block.is_empty]
        text_height = sum(block.height for block in blocks) + gap * max(len(blocks) - 1, 0)

        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        fade_height = int(height * style.scrim_fade_ratio)
        scrim_height = min(text_height + margin * 2 + fade_height, height)
        self._paste_scrim(overlay, scrim_height, fade_height)

        draw = ImageDraw.Draw(overlay)
        cursor_y = height - margin - text_height
        for block in blocks:
            color = self._color_for(block, quote_block, author_block)
            cursor_y = self._draw_block(draw, block, cursor_y, margin, content_width, color, height)
            cursor_y += gap

        composed = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
        log.info(
            "composited %d quote line(s) at %dpx onto %dx%d image",
            len(quote_block.lines),
            quote_block.font.size,
            width,
            height,
        )
        return RenderedOverlay(
            image=composed,
            quote_lines=quote_block.lines,
            credit_line=credit_line,
            font_path=self.fonts.resolve(),
            quote_font_size=int(quote_block.font.size),
        )

    @staticmethod
    def _open_normalised(image_path: Path) -> Image.Image:
        """Open an image with its EXIF rotation applied and no alpha channel.

        Commons holds plenty of phone photographs whose pixels are sideways and
        whose correct orientation lives only in an EXIF tag. Without this the
        overlay text ends up running up the side of the picture.
        """
        with Image.open(image_path) as handle:
            handle.load()
            return ImageOps.exif_transpose(handle).convert("RGB")

    def _quote_text(self, quote: Quote) -> str:
        text = quote.text.strip()
        if self.style.wrap_in_quote_marks and text:
            return f"“{text}”"
        return text

    @staticmethod
    def _byline_text(quote: Quote) -> str:
        """The line under the quote: the untranslated original, then the author.

        The original is what makes a garbled translation readable as a joke, so
        it gets the byline even though proverbs have no author to fill it.
        """
        parts = []
        if quote.original_text:
            parts.append(f"Originally: “{quote.original_text.strip()}”")
        if quote.author:
            parts.append(f"— {quote.author.strip()}")
        return " ".join(parts)

    def _single_line_block(
        self, text: str, content_width: int, size_ratio: float, image_height: int
    ) -> TextBlock:
        """Wrap a short line, shrinking only if it genuinely does not fit."""
        if not text.strip():
            empty_font = self.fonts.load(max(int(image_height * size_ratio), 10))
            return TextBlock((), empty_font, 0, 0, 0)

        return fit_text(
            text,
            resolver=self.fonts,
            max_width=content_width,
            # Two lines of headroom: a long credit line is allowed to wrap.
            max_height=int(image_height * size_ratio * 3),
            start_size=max(int(image_height * size_ratio), 10),
            line_spacing=1.15,
        )

    def _paste_scrim(self, overlay: Image.Image, scrim_height: int, fade_height: int) -> None:
        """Lay a bottom-anchored scrim onto the overlay layer.

        Alpha ramps from nothing to `scrim_opacity` across `fade_height`, then
        holds flat for the rest -- so the text region is uniformly backed.
        """
        width, height = overlay.size
        if scrim_height <= 1:
            return

        style = self.style
        peak = int(style.scrim_opacity * 255)
        fade = min(max(fade_height, 0), scrim_height)

        ramp = Image.new("L", (1, scrim_height))
        ramp.putdata(
            [
                int(peak * (y / fade) ** style.scrim_easing) if y < fade else peak
                for y in range(scrim_height)
            ]
        )

        scrim = Image.new("RGBA", (width, scrim_height), (*style.scrim_color, 0))
        scrim.putalpha(ramp.resize((width, scrim_height)))
        overlay.alpha_composite(scrim, (0, height - scrim_height))

    def _color_for(
        self, block: TextBlock, quote_block: TextBlock, author_block: TextBlock
    ) -> tuple[int, int, int, int]:
        if block is quote_block:
            return self.style.text_color
        if block is author_block:
            return self.style.author_color
        return self.style.credit_color

    def _draw_block(
        self,
        draw: ImageDraw.ImageDraw,
        block: TextBlock,
        top: int,
        margin: int,
        content_width: int,
        color: tuple[int, int, int, int],
        image_height: int,
    ) -> int:
        """Draw one wrapped block and return the y coordinate just below it."""
        shadow_offset = max(int(image_height * self.style.shadow_offset_ratio), 1)
        measurer = TextMeasurer(block.font, self.style.line_spacing)
        y = top

        for line in block.lines:
            if self.style.align == "center":
                x = margin + (content_width - measurer.text_width(line)) // 2
            else:
                x = margin

            draw.text(
                (x + shadow_offset, y + shadow_offset),
                line,
                font=block.font,
                fill=self.style.shadow_color,
            )
            draw.text((x, y), line, font=block.font, fill=color)
            y += block.line_height

        return y
