"""The daily job.

This module is the only place that knows the order of operations. Every
dependency is injected rather than constructed inline, which is what makes
testing possible without network access: hand the pipeline a fake Commons client
and a temp directory and the whole thing runs offline.

Ordering is chosen so that an interrupted run is always detected as incomplete.
The final image is written last, because `OutputStore.is_complete()` needs *all*
published files present -- so a crash after the attribution but before the image
leaves the day correctly marked unfinished.
"""

from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from daily_proverb_overlay import __version__
from daily_proverb_overlay.attribution import AttributionFormatter
from daily_proverb_overlay.config import Settings
from daily_proverb_overlay.http_client import HttpClient
from daily_proverb_overlay.models import PictureOfTheDay, Quote
from daily_proverb_overlay.render.compositor import OverlayCompositor, OverlayStyle, RenderedOverlay
from daily_proverb_overlay.render.fonts import FontResolver
from daily_proverb_overlay.sources.quotes import QuoteProvider, get_quote_provider
from daily_proverb_overlay.sources.translation import GoogleCloudTranslator, TranslationChain
from daily_proverb_overlay.sources.wikimedia import CommonsPotdClient
from daily_proverb_overlay.storage import OutputPaths, OutputStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    """What one run did."""

    potd_date: date
    paths: OutputPaths
    skipped: bool = False
    """True when complete output already existed and `force` was not set."""

    picture: PictureOfTheDay | None = None
    quote: Quote | None = None


class PotdOverlayPipeline:
    """Fetch, render, and write one day's image."""

    def __init__(
        self,
        *,
        http: HttpClient,
        potd_client: CommonsPotdClient,
        quote_provider: QuoteProvider,
        compositor: OverlayCompositor,
        store: OutputStore,
        translation_chain: TranslationChain | None = None,
        jpeg_quality: int = 90,
    ) -> None:
        self.http = http
        self.potd_client = potd_client
        self.quote_provider = quote_provider
        self.translation_chain = translation_chain
        self.compositor = compositor
        self.store = store
        self.jpeg_quality = jpeg_quality

    def run(self, potd_date: date, *, force: bool = False, dry_run: bool = False) -> PipelineResult:
        """Produce the overlay for `potd_date`.

        Args:
            potd_date: which day's POTD to fetch.
            force: rebuild even if complete output already exists.
            dry_run: fetch metadata and report, but write nothing.
        """
        paths = self.store.paths_for(potd_date)

        if self.store.is_complete(paths) and not force:
            log.info("%s already complete at %s, skipping", potd_date, paths.directory)
            return PipelineResult(potd_date=potd_date, paths=paths, skipped=True)

        picture = self.potd_client.fetch(potd_date)
        log.info(
            "POTD %s: %s (%dx%d)",
            potd_date,
            picture.file_title,
            picture.width,
            picture.height,
        )

        quote = self.quote_provider.fetch(potd_date)
        if quote.is_placeholder:
            log.warning("overlay text is placeholder filler, not a real quote")
        if self.translation_chain is not None:
            quote = self.translation_chain.apply(quote)

        # Re-resolve paths now that the MIME type is known, so the cached source
        # keeps a truthful extension.
        paths = self.store.paths_for(potd_date, _suffix_for(picture))
        formatter = AttributionFormatter(picture.attribution)

        if dry_run:
            log.info("dry run, writing nothing. Attribution would be:\n%s", formatter.full_text())
            return PipelineResult(potd_date=potd_date, paths=paths, picture=picture, quote=quote)

        self.store.ensure_directory(paths)
        self.store.clean_partials(paths)

        source = self._ensure_source_image(picture, paths, force=force)
        rendered = self.compositor.render(source, quote, formatter.credit_line())

        # Attribution and metadata first, final image last -- see module docstring.
        self.store.write_text(paths.attribution, formatter.full_text() + "\n")
        if rendered.size != (picture.width, picture.height):
            # Commons rounds the requested width up to a cached bucket, so the
            # file served is routinely larger than the imageinfo response claims.
            log.debug(
                "served thumbnail is %dx%d, imageinfo reported %dx%d",
                *rendered.size,
                picture.width,
                picture.height,
            )

        self.store.write_json(paths.metadata, _metadata(picture, quote, formatter, rendered))
        self.store.write_image(
            paths.final_image,
            rendered.image,
            quality=self.jpeg_quality,
            optimize=True,
            progressive=True,
        )

        log.info("wrote %s", paths.final_image)
        return PipelineResult(potd_date=potd_date, paths=paths, picture=picture, quote=quote)

    def _ensure_source_image(
        self, picture: PictureOfTheDay, paths: OutputPaths, *, force: bool
    ) -> Path:
        """Download the source image unless a cached copy is already present.

        Reusing the cache keeps a re-render (new style, new quote) from hitting
        Wikimedia again, which is both faster and politer.
        """
        if paths.source_image.is_file() and paths.source_image.stat().st_size > 0 and not force:
            log.info("reusing cached source image %s", paths.source_image)
            return paths.source_image

        return self.http.download(picture.image_url, paths.source_image)


def build_pipeline(settings: Settings, style: OverlayStyle | None = None) -> PotdOverlayPipeline:
    """Construct a pipeline from settings.

    The caller owns the returned pipeline's `http` client and should close it.
    """
    http = HttpClient(
        settings.user_agent,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
        backoff_factor=settings.backoff_factor,
    )
    style = style or OverlayStyle()

    return PotdOverlayPipeline(
        http=http,
        potd_client=CommonsPotdClient(http, thumbnail_width=settings.thumbnail_width),
        quote_provider=get_quote_provider(settings.quote_provider, http),
        compositor=OverlayCompositor(FontResolver(settings.font_path), style),
        store=OutputStore(settings.output_dir),
        translation_chain=_translation_chain(settings, http),
        jpeg_quality=style.jpeg_quality,
    )


def _translation_chain(settings: Settings, http: HttpClient) -> TranslationChain | None:
    """The configured chain, or None when it is turned off or has no key yet."""
    if not settings.translation_languages:
        return None
    if not settings.google_translate_api_key:
        log.warning(
            "POTD_GOOGLE_TRANSLATE_API_KEY is not set, so the proverb will not be "
            'translated. Set it, or pass --languages "" to turn translation off '
            "and silence this warning."
        )
        return None

    translator = GoogleCloudTranslator(http, settings.google_translate_api_key)
    return TranslationChain(translator, settings.translation_languages)


def _suffix_for(picture: PictureOfTheDay) -> str:
    """File extension for the cached source, derived from the served MIME type."""
    if picture.mime:
        guessed = mimetypes.guess_extension(picture.mime)
        if guessed:
            return ".jpeg" if guessed == ".jpe" else guessed
    return ".jpg"


def _metadata(
    picture: PictureOfTheDay,
    quote: Quote,
    formatter: AttributionFormatter,
    rendered: RenderedOverlay,
) -> dict[str, object]:
    """The sidecar record: enough to reproduce or audit this output."""
    width, height = rendered.size
    return {
        "generator": {"name": "daily-proverb-overlay", "version": __version__},
        "date": picture.potd_date.isoformat(),
        "source": {
            "file_title": picture.file_title,
            "image_url": picture.image_url,
            # What imageinfo claimed. The file actually served can be larger --
            # see `render.width`/`render.height` for the real dimensions.
            "reported_width": picture.width,
            "reported_height": picture.height,
            "mime": picture.mime,
        },
        "quote": {
            "text": quote.text,
            "author": quote.author,
            "source": quote.source,
            "provider": quote.provider,
            "is_placeholder": quote.is_placeholder,
            "original_text": quote.original_text,
            "translations": [
                {"language": step.language, "text": step.text} for step in quote.translations
            ],
        },
        "attribution": formatter.to_dict(),
        "render": {
            "font": str(rendered.font_path),
            "quote_font_size": rendered.quote_font_size,
            "quote_lines": len(rendered.quote_lines),
            "width": width,
            "height": height,
        },
    }
