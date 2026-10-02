"""Building the static site that GitHub Pages serves.

Reads every finished day under the output directory -- finished meaning
`OutputStore.is_complete()`, the same rule the daily job skips on -- and writes:

    _site/
    ├── index.html              the newest day, then links to every earlier one
    └── YYYY-MM-DD/
        ├── index.html          that day's page
        └── overlay.jpg         copied from output/

The site is rebuilt from scratch on every run rather than patched: `output/` is
the source of truth, and a full rebuild means nothing stale can survive. At a few
hundred days it still takes well under a second.

The page carries the full attribution, with links. The credit line burned into
the image cannot hold a link, and CC BY-SA requires one to the license.

Everything that came from an external API -- titles, artist names, proverbs,
URLs -- is escaped on the way into HTML. Commons metadata is uploader-written,
so it is untrusted input like any other.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from daily_proverb_overlay.attribution import (
    DERIVATIVE_NOTICE,
    SHARE_ALIKE_NOTICE,
    UNKNOWN_AUTHOR,
    UNKNOWN_LICENSE,
    UNKNOWN_TITLE,
)
from daily_proverb_overlay.config import DEFAULT_OUTPUT_DIR
from daily_proverb_overlay.models import TranslationStep
from daily_proverb_overlay.storage import FINAL_IMAGE_NAME, OutputStore

log = logging.getLogger(__name__)

DEFAULT_SITE_DIR = Path("_site")
"""Where `actions/upload-pages-artifact` looks unless told otherwise."""

SITE_MARKER = ".daily-proverb-site"
"""Marks a directory as one this module built and may therefore wipe.

Without it, a mistyped `--site-dir` pointing at real files would get them
deleted by the rebuild.
"""

SITE_TITLE = "Daily Proverb Overlay"

LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "sw": "Swahili",
    "ka": "Georgian",
    "fi": "Finnish",
    "zh-CN": "Chinese",
    "eu": "Basque",
    "mg": "Malagasy",
    "th": "Thai",
}
"""Display names for the chain. Unlisted codes are shown as the code itself."""


class SiteError(RuntimeError):
    """The site could not be built."""


@dataclass(frozen=True)
class ArchiveDay:
    """One finished day, reduced to what a page shows."""

    day: date
    image: Path
    width: int | None
    height: int | None

    text: str
    original_text: str | None
    proverb_url: str | None
    translations: tuple[TranslationStep, ...]

    title: str
    artist: str
    credit: str | None
    license_name: str
    license_url: str | None
    file_page_url: str | None
    share_alike: bool

    @property
    def slug(self) -> str:
        return self.day.isoformat()

    @property
    def long_date(self) -> str:
        """`3 October 2026`, built by hand because `%-d` is not portable."""
        return f"{self.day.day} {self.day:%B} {self.day.year}"


def load_days(output_dir: Path) -> list[ArchiveDay]:
    """Every finished day under `output_dir`, newest first.

    Unfinished days are skipped silently, unreadable ones with a warning: one
    bad folder should not take the whole archive offline.
    """
    if not output_dir.is_dir():
        return []

    store = OutputStore(output_dir)
    days: list[ArchiveDay] = []
    for directory in sorted(output_dir.iterdir()):
        try:
            day = date.fromisoformat(directory.name)
        except ValueError:
            continue

        paths = store.paths_for(day)
        if not store.is_complete(paths):
            log.debug("skipping %s: not finished", day)
            continue

        try:
            days.append(_read_day(day, paths.metadata, paths.final_image))
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("skipping %s: unreadable %s (%s)", day, paths.metadata.name, exc)

    return sorted(days, key=lambda archive_day: archive_day.day, reverse=True)


def build_site(output_dir: Path, site_dir: Path) -> int:
    """Rebuild the whole site from `output_dir`. Returns the number of days.

    Raises:
        SiteError: there is nothing to publish, or `site_dir` holds files this
            module did not create.
    """
    days = load_days(output_dir)
    if not days:
        raise SiteError(f"no finished days under {output_dir}, so there is nothing to publish")

    _reset_site_dir(site_dir)

    for index, day in enumerate(days):
        newer = days[index - 1] if index > 0 else None
        older = days[index + 1] if index + 1 < len(days) else None

        day_dir = site_dir / day.slug
        day_dir.mkdir()
        shutil.copyfile(day.image, day_dir / FINAL_IMAGE_NAME)
        _write(day_dir / "index.html", _day_page(day, newer=newer, older=older))

    _write(site_dir / "index.html", _index_page(days))
    log.info("built %d day page(s) into %s, newest %s", len(days), site_dir, days[0].day)
    return len(days)


def _read_day(day: date, metadata_path: Path, image_path: Path) -> ArchiveDay:
    meta: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
    quote = meta.get("quote") or {}
    credit = meta.get("attribution") or {}
    render = meta.get("render") or {}

    return ArchiveDay(
        day=day,
        image=image_path,
        width=_int_or_none(render.get("width")),
        height=_int_or_none(render.get("height")),
        text=str(quote["text"]),
        original_text=quote.get("original_text") or None,
        proverb_url=quote.get("source") or None,
        # Days rendered before the translation chain existed have no hops.
        translations=tuple(
            TranslationStep(language=str(step["language"]), text=str(step["text"]))
            for step in quote.get("translations") or ()
        ),
        title=credit.get("title") or UNKNOWN_TITLE,
        artist=credit.get("artist") or UNKNOWN_AUTHOR,
        credit=credit.get("credit") or None,
        license_name=credit.get("license_short_name") or UNKNOWN_LICENSE,
        license_url=credit.get("license_url") or None,
        file_page_url=credit.get("file_page_url") or None,
        share_alike=bool(credit.get("share_alike")),
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _reset_site_dir(site_dir: Path) -> None:
    if site_dir.exists():
        if not (site_dir / SITE_MARKER).is_file() and any(site_dir.iterdir()):
            raise SiteError(
                f"{site_dir} is not empty and was not built by this tool; "
                "refusing to delete it. Pick another --site-dir."
            )
        shutil.rmtree(site_dir)

    site_dir.mkdir(parents=True)
    (site_dir / SITE_MARKER).write_text("Built by daily-proverb-site. Safe to delete.\n")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


# --- HTML -------------------------------------------------------------------


def _index_page(days: Sequence[ArchiveDay]) -> str:
    latest, earlier = days[0], days[1:]
    body = _day_section(latest, image_src=f"{latest.slug}/{FINAL_IMAGE_NAME}")

    if earlier:
        items = "\n".join(
            f'      <li><a href="{day.slug}/">{_e(day.long_date)}</a>'
            f'<span class="teaser">“{_e(day.text)}”</span></li>'
            for day in earlier
        )
        body += f"""
  <section class="archive">
    <h2>Earlier days</h2>
    <ul>
{items}
    </ul>
  </section>"""

    return _page(SITE_TITLE, body, home_href="./")


def _day_page(day: ArchiveDay, *, newer: ArchiveDay | None, older: ArchiveDay | None) -> str:
    links = ['<a href="../">All days</a>']
    if older is not None:
        links.append(f'<a href="../{older.slug}/">← {_e(older.long_date)}</a>')
    if newer is not None:
        links.append(f'<a href="../{newer.slug}/">{_e(newer.long_date)} →</a>')

    body = _day_section(day, image_src=FINAL_IMAGE_NAME)
    body += f'\n  <nav class="pager">{" · ".join(links)}</nav>'
    return _page(f"{day.long_date} · {SITE_TITLE}", body, home_href="../")


def _day_section(day: ArchiveDay, *, image_src: str) -> str:
    size = f' width="{day.width}" height="{day.height}"' if day.width and day.height else ""
    alt = f"“{day.text}” written over {day.title}"

    original = ""
    if day.original_text:
        proverb = _link(day.proverb_url, f"“{day.original_text}”")
        original = f'\n    <p class="original">Originally {proverb}</p>'

    return f"""
  <article>
    <h2><time datetime="{day.slug}">{_e(day.long_date)}</time></h2>
    <img src="{_e(image_src)}"{size} alt="{_e(alt)}">{original}{_translations(day)}
    <section class="credit">
      <h3>Image credit</h3>
      <p>{_link(day.file_page_url, day.title)} by {_e(day.artist)}.{_credit(day)}
        License: {_link(day.license_url, day.license_name)}.</p>
      <p>{_e(SHARE_ALIKE_NOTICE if day.share_alike else DERIVATIVE_NOTICE)}</p>
    </section>
  </article>"""


def _translations(day: ArchiveDay) -> str:
    if not day.translations:
        return ""
    steps = "\n".join(
        f'        <li><span class="lang">{_e(LANGUAGE_NAMES.get(step.language, step.language))}'
        f'</span> <span lang="{_e(step.language)}">{_e(step.text)}</span></li>'
        for step in day.translations
    )
    return f"""
    <details>
      <summary>How it got here</summary>
      <ol>
{steps}
      </ol>
    </details>"""


def _credit(day: ArchiveDay) -> str:
    return f" {_e(day.credit.rstrip('.'))}." if day.credit else ""


def _page(title: str, body: str, *, home_href: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
  <header><a href="{home_href}">{_e(SITE_TITLE)}</a></header>{body}
  <footer>
    Pictures: Wikimedia Commons Picture of the Day.
    Proverbs: Wiktionary. Translations: Google Cloud Translation.
  </footer>
</main>
</body>
</html>
"""


def _link(url: str | None, label: str) -> str:
    """An anchor for http(s) URLs only; anything else is shown as plain text.

    The scheme check matters because the URL is uploader-supplied: a
    `javascript:` link would otherwise run in the visitor's browser.
    """
    if url and url.startswith(("https://", "http://")):
        return f'<a href="{_e(url)}">{_e(label)}</a>'
    return _e(label)


def _e(text: str) -> str:
    return html.escape(text, quote=True)


_CSS = """
  :root { color-scheme: dark; --bg: #121212; --fg: #ececec; --muted: #9b9b9b; --link: #8ab4f8; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg); overflow-wrap: anywhere;
         font: 17px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 900px; margin: 0 auto; padding: 20px 16px 48px; }
  header a { color: var(--muted); text-decoration: none; font-weight: 600; letter-spacing: .03em; }
  h2 { font-size: 1.4rem; margin: 18px 0 12px; }
  h3 { font-size: 1rem; margin: 0 0 6px; color: var(--muted); }
  img { display: block; width: 100%; height: auto; border-radius: 6px; background: #000; }
  a { color: var(--link); }
  .original { font-size: 1.1rem; margin: 14px 0 6px; }
  details { margin: 8px 0 18px; color: var(--muted); }
  summary { cursor: pointer; }
  details ol { padding-left: 1.4em; }
  details li { margin: 4px 0; }
  .lang { display: inline-block; min-width: 6.5em; color: var(--fg); }
  .credit { font-size: .92rem; color: var(--muted); border-top: 1px solid #2a2a2a;
            padding-top: 12px; margin-top: 12px; }
  .credit p { margin: 4px 0; }
  .archive ul { list-style: none; padding: 0; }
  .archive li { padding: 8px 0; border-bottom: 1px solid #222; }
  .teaser { display: block; color: var(--muted); font-size: .95rem; }
  .pager { margin-top: 24px; }
  footer { margin-top: 40px; font-size: .85rem; color: var(--muted); }
"""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="daily-proverb-site",
        description="Build the static archive site from the finished days in the output directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Daily output to publish (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        default=DEFAULT_SITE_DIR,
        help=f"Where to write the site; rebuilt from scratch (default: {DEFAULT_SITE_DIR})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log debug detail")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        count = build_site(args.output_dir, args.site_dir)
    except SiteError as exc:
        log.error("%s", exc)
        return 1

    print(f"built {count} day page(s) into {args.site_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
