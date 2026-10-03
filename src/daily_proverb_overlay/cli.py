"""Command line entry point.

Kept thin on purpose: parse arguments, configure logging, call the pipeline, and
translate exceptions into exit codes. All the interesting behaviour lives in the
modules it calls, so this file stays boring as the project grows.

Exit codes exist for the benefit of CI -- a workflow step needs to tell
"no POTD published yet" apart from "the job is broken":

    0  success, or a skipped day that was already complete
    1  unexpected failure
    2  bad invocation (argparse)
    3  no POTD published for the requested date
    4  configuration problem, e.g. missing contact info, no translation key, or no font
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from daily_proverb_overlay import __version__
from daily_proverb_overlay.config import (
    DEFAULT_TRANSLATION_LANGUAGES,
    ConfigError,
    Settings,
    parse_languages,
)
from daily_proverb_overlay.http_client import HttpError
from daily_proverb_overlay.pipeline import PipelineResult, build_pipeline
from daily_proverb_overlay.render.fonts import FontNotFoundError
from daily_proverb_overlay.sources.quotes import available_providers
from daily_proverb_overlay.sources.translation import TranslationError
from daily_proverb_overlay.sources.wikimedia import PictureOfTheDayNotFound, WikimediaError
from daily_proverb_overlay.sources.wiktionary import WiktionaryError

log = logging.getLogger("daily_proverb_overlay")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NOT_PUBLISHED = 3
EXIT_CONFIG = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily-proverb-overlay",
        description="Overlay a quote onto the Wikimedia Commons Picture of the Day.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="POTD date to render (default: today, UTC -- Commons schedules by UTC day)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write output (default: ./output, or POTD_OUTPUT_DIR)",
    )
    parser.add_argument(
        "--contact",
        default=None,
        help=(
            "Repo URL or email for the User-Agent. Required by Wikimedia policy; "
            "may also be set via POTD_CONTACT."
        ),
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        dest="thumbnail_width",
        help="Thumbnail width to request from Commons (default: 1600)",
    )
    parser.add_argument(
        "--font",
        type=Path,
        default=None,
        dest="font_path",
        help="TrueType font file to render with (default: bundled, then system fonts)",
    )
    parser.add_argument(
        "--quote-provider",
        default=None,
        choices=available_providers(),
        help="Source of the overlay text (default: wiktionary; lorem is offline filler)",
    )
    parser.add_argument(
        "--languages",
        type=parse_languages,
        default=None,
        dest="translation_languages",
        metavar="CODES",
        help=(
            "Comma-separated language codes to translate through before coming back "
            f"to English (default: {','.join(DEFAULT_TRANSLATION_LANGUAGES)}). "
            'Pass "" to skip translation. '
            "Needs POTD_GOOGLE_TRANSLATE_API_KEY."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download and re-render even if the day's output is already complete",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch metadata and print the attribution, but write no files",
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="Log debug detail, including HTTP calls"
    )
    verbosity.add_argument(
        "-q", "--quiet", action="store_true", help="Log warnings and errors only"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(verbose=args.verbose, quiet=args.quiet)

    potd_date: date = args.date or datetime.now(timezone.utc).date()

    try:
        settings = Settings.resolve(
            contact=args.contact,
            output_dir=args.output_dir,
            thumbnail_width=args.thumbnail_width,
            font_path=args.font_path,
            quote_provider=args.quote_provider,
            translation_languages=args.translation_languages,
        )
    except ConfigError as exc:
        log.error("%s", exc)
        return EXIT_CONFIG

    pipeline = build_pipeline(settings)
    try:
        result = pipeline.run(potd_date, force=args.force, dry_run=args.dry_run)
    except PictureOfTheDayNotFound as exc:
        log.error("%s", exc)
        return EXIT_NOT_PUBLISHED
    except FontNotFoundError as exc:
        log.error("%s", exc)
        return EXIT_CONFIG
    except (WikimediaError, WiktionaryError, TranslationError, HttpError) as exc:
        log.error("%s", exc)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        log.warning("interrupted; partial files will be cleaned up on the next run")
        return EXIT_FAILURE
    finally:
        pipeline.http.close()

    _report(result, dry_run=args.dry_run)
    return EXIT_OK


def _report(result: PipelineResult, dry_run: bool) -> None:
    if result.skipped:
        print(f"{result.potd_date}: already complete, nothing to do ({result.paths.directory})")
        return

    if dry_run:
        print(f"{result.potd_date}: dry run, no files written")
    else:
        print(f"{result.potd_date}: wrote {result.paths.final_image}")
        print(f"  attribution: {result.paths.attribution}")
        print(f"  metadata:    {result.paths.metadata}")

    quote = result.quote
    if quote is None:
        return
    print(f"  quote:       {quote.text}")
    if quote.original_text:
        print(f"  originally:  {quote.original_text}")
    if quote.is_placeholder:
        print("  note: overlay text is placeholder filler, not a real quote")


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _configure_logging(*, verbose: bool, quiet: bool) -> None:
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO

    # Translations can come back with words left in the source script. A console
    # or CI log stuck on a legacy code page must not crash the run over that.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")

    logging.basicConfig(
        level=level,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # urllib3 logs every retry at INFO, which drowns out our own output.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


if __name__ == "__main__":
    raise SystemExit(main())
