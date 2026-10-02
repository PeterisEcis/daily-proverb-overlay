"""Fetching the Picture of the Day from Wikimedia Commons.

Commons publishes each day's pick as a template page, `Template:Potd/YYYY-MM-DD`,
whose only job is to transclude that day's file. So one Action API call does the
whole lookup: `generator=images` walks from the template to the file it embeds,
and `prop=imageinfo` returns that file's URLs and metadata in the same response.

Two details matter and are easy to get wrong:

* `iiurlwidth` is what makes this affordable. Without it the API hands back the
  original, and POTD originals are routinely 50-100MB TIFFs.
* `extmetadata` values are HTML fragments, not plain text -- `Artist` is usually
  an anchor tag. They are unescaped and stripped here, at the boundary, so that
  nothing downstream has to know the API returns markup.
"""

from __future__ import annotations

import logging
from datetime import date
from html.parser import HTMLParser
from typing import Any

from daily_proverb_overlay.http_client import HttpClient
from daily_proverb_overlay.models import Attribution, PictureOfTheDay

log = logging.getLogger(__name__)

API_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
POTD_TEMPLATE = "Template:Potd/{:%Y-%m-%d}"


class WikimediaError(RuntimeError):
    """The API answered, but not with something usable."""


class PictureOfTheDayNotFound(WikimediaError):
    """No POTD is published for the requested date."""


class CommonsPotdClient:
    """Resolves a date to a downloadable POTD plus its attribution."""

    def __init__(
        self,
        http: HttpClient,
        *,
        thumbnail_width: int = 1600,
        endpoint: str = API_ENDPOINT,
        metadata_language: str = "en",
    ) -> None:
        self.http = http
        self.thumbnail_width = thumbnail_width
        self.endpoint = endpoint
        self.metadata_language = metadata_language

    def fetch(self, potd_date: date) -> PictureOfTheDay:
        """Return the POTD for `potd_date`.

        Raises:
            PictureOfTheDayNotFound: the template page has no image, or does not
                exist yet. Commons schedules POTD ahead of time, but not far, so
                a future date legitimately has nothing.
            WikimediaError: the response was shaped unexpectedly.
        """
        template = POTD_TEMPLATE.format(potd_date)
        payload = self._query(template)
        page = self._select_image_page(payload, template)
        return self._to_picture(page, potd_date)

    def _query(self, template: str) -> dict[str, Any]:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "images",
            "titles": template,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": self.thumbnail_width,
            "iiextmetadatalanguage": self.metadata_language,
        }
        payload = self.http.get_json(self.endpoint, params)

        if "error" in payload:
            detail = payload["error"].get("info", payload["error"])
            raise WikimediaError(f"Commons API error for {template}: {detail}")
        return payload

    def _select_image_page(self, payload: dict[str, Any], template: str) -> dict[str, Any]:
        """Pick the POTD out of the images the template embeds.

        A template usually embeds exactly one file, but maintenance banners and
        license icons do show up. Choosing the largest image by pixel count is
        robust against that without hardcoding a list of icons to ignore.
        """
        pages = payload.get("query", {}).get("pages")
        if not pages:
            raise PictureOfTheDayNotFound(
                f"{template} embeds no images -- no POTD published for that date yet"
            )

        candidates = [page for page in pages if page.get("imageinfo") and not page.get("missing")]
        if not candidates:
            raise PictureOfTheDayNotFound(f"{template} returned no usable image info")

        if len(candidates) > 1:
            log.debug(
                "%s embeds %d images, picking the largest: %s",
                template,
                len(candidates),
                [page.get("title") for page in candidates],
            )

        return max(candidates, key=_pixel_count)

    def _to_picture(self, page: dict[str, Any], potd_date: date) -> PictureOfTheDay:
        info = page["imageinfo"][0]

        # thumburl is absent for formats the thumbnailer cannot render; falling
        # back to the original keeps the job running, just at full size.
        image_url = info.get("thumburl") or info.get("url")
        if not image_url:
            raise WikimediaError(f"{page.get('title')} has no usable image URL")

        if info.get("thumburl"):
            width = int(info.get("thumbwidth", 0))
            height = int(info.get("thumbheight", 0))
        else:
            log.warning(
                "no thumbnail available for %s, falling back to the original",
                page.get("title"),
            )
            width = int(info.get("width", 0))
            height = int(info.get("height", 0))

        return PictureOfTheDay(
            potd_date=potd_date,
            file_title=str(page.get("title", "")),
            image_url=image_url,
            width=width,
            height=height,
            mime=info.get("mime"),
            attribution=self._to_attribution(page, info),
        )

    def _to_attribution(self, page: dict[str, Any], info: dict[str, Any]) -> Attribution:
        meta = info.get("extmetadata", {})
        file_title = str(page.get("title", ""))

        return Attribution(
            file_page_url=info.get("descriptionurl", ""),
            # ObjectName is the curated title; the file name is a usable fallback.
            title=_meta(meta, "ObjectName") or _title_from_filename(file_title),
            artist=_meta(meta, "Artist"),
            credit=_meta(meta, "Credit"),
            license_short_name=_meta(meta, "LicenseShortName"),
            license_url=_meta(meta, "LicenseUrl"),
        )


def _pixel_count(page: dict[str, Any]) -> int:
    info = page.get("imageinfo", [{}])[0]
    return int(info.get("width", 0)) * int(info.get("height", 0))


def _meta(extmetadata: dict[str, Any], key: str) -> str | None:
    """Read one `extmetadata` field as plain text, or None if absent/blank."""
    field = extmetadata.get(key)
    if not isinstance(field, dict):
        return None

    value = field.get("value")
    if not isinstance(value, str):
        return None

    text = html_to_text(value)
    return text or None


def _title_from_filename(file_title: str) -> str | None:
    """`File:Some photo.jpg` -> `Some photo`."""
    if not file_title:
        return None
    stem = file_title.removeprefix("File:")
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return stem.replace("_", " ").strip() or None


class _TextExtractor(HTMLParser):
    """Collects text nodes, dropping tags.

    `extmetadata` holds small HTML fragments -- links, spans, the occasional
    `<br>`. A real parser is used rather than a regex because the fragments are
    uploader-authored and routinely malformed.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Preserve the word break that a block-level tag implies.
        if tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append(" ")

    def text(self) -> str:
        return " ".join("".join(self.parts).split())


def html_to_text(fragment: str) -> str:
    """Flatten an HTML fragment to single-spaced plain text."""
    extractor = _TextExtractor()
    extractor.feed(fragment)
    extractor.close()
    return extractor.text()
