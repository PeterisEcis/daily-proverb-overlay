"""Settings for a single run.

Values come from, in order of precedence:

1. CLI flags
2. real environment variables -- how CI supplies them, as secrets
3. a `.env` file in the working directory -- for local runs; see `.env.example`
4. the defaults here

The one secret, the translation API key, has no CLI flag: a flag would leave it
in shell history and the process list.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import dotenv_values

from daily_proverb_overlay import __version__

ENV_PREFIX = "POTD_"
DOTENV_PATH = Path(".env")

DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_THUMBNAIL_WIDTH = 1600
"""Raw POTD files are routinely 50-100MB TIFFs. Always ask for a thumbnail."""

DEFAULT_TRANSLATION_LANGUAGES: tuple[str, ...] = (
    "ja",
    "sw",
    "ka",
    "fi",
    "zh-CN",
    "eu",
    "mg",
    "th",
)
"""Eight language families, each losing something English needs to get back:

* `ja` Japanese (Japonic): no articles or plurals, and subjects get dropped.
* `sw` Swahili (Niger-Congo): a noun-class system English has nothing like.
* `ka` Georgian (Kartvelian): one verb form carries subject, object and tense.
* `fi` Finnish (Uralic): one pronoun for he and she, so gender gets reassigned.
* `zh-CN` Chinese (Sino-Tibetan): no tense, plurals or articles at all.
* `eu` Basque (an isolate, related to nothing): ergative grammar, and less
  training data than most.
* `mg` Malagasy (Austronesian): verb-object-subject order, the reverse of
  English.
* `th` Thai (Kra-Dai): no tense or plural marking, so the final hop back into
  English has to guess both.

The order keeps Chinese well away from Japanese, which shares its characters:
a hop between the two would likely carry more meaning across than the rest.
"""


MISSING_CONTACT_MESSAGE = (
    "No contact information configured. Wikimedia's User-Agent policy requires a "
    "descriptive agent that includes a way to reach the operator, and blocks "
    "requests that lack one.\n"
    "Set POTD_CONTACT in .env (copy .env.example to start one), or for the session:\n"
    '  $env:POTD_CONTACT = "https://github.com/you/pipelines"\n'
    "or pass it per run:\n"
    "  --contact https://github.com/you/pipelines"
)

MISSING_API_KEY_MESSAGE = (
    "Translation is on, but no API key is configured. Without one the proverb would be "
    "published untranslated, and a finished day is never redone, so the run stops "
    "instead.\n"
    "Set POTD_GOOGLE_TRANSLATE_API_KEY in .env (in CI, as a repository secret), or turn "
    "translation off on purpose for the run:\n"
    '  --languages ""\n'
    "or for good, with an empty POTD_TRANSLATION_LANGUAGES= in .env."
)


class ConfigError(ValueError):
    """Raised when settings are missing or unusable."""


@dataclass(frozen=True)
class Settings:
    contact: str
    """Repo URL or email, embedded in the User-Agent.

    Wikimedia's User-Agent policy requires a descriptive agent with a way to
    reach the operator; generic or absent agents are blocked outright. This is
    required rather than defaulted on purpose -- a silently generic UA fails
    with a confusing 403 much later.
    """

    output_dir: Path = DEFAULT_OUTPUT_DIR
    thumbnail_width: int = DEFAULT_THUMBNAIL_WIDTH
    request_timeout: float = 30.0
    max_retries: int = 4
    backoff_factor: float = 1.0
    """Exponential backoff base, in seconds: 1, 2, 4, 8 ..."""

    font_path: Path | None = None
    quote_provider: str = "wiktionary"

    translation_languages: tuple[str, ...] = DEFAULT_TRANSLATION_LANGUAGES
    """Languages the quote passes through on its way back to English. Empty
    disables the chain."""

    google_translate_api_key: str | None = field(default=None, repr=False)
    """Kept out of `repr` so that logging the settings never leaks it.

    Required whenever `translation_languages` is non-empty; `resolve()` enforces
    that.
    """

    @property
    def user_agent(self) -> str:
        return f"DailyProverbOverlay/{__version__} ({self.contact})"

    def merged_with(self, **overrides: object) -> Settings:
        """Return a copy with the non-None overrides applied.

        Lets the CLI pass every flag through without caring which ones the user
        actually supplied.
        """
        supplied = {key: value for key, value in overrides.items() if value is not None}
        return replace(self, **supplied)  # type: ignore[arg-type]

    @classmethod
    def resolve(
        cls,
        env: Mapping[str, str] | None = None,
        **overrides: object,
    ) -> Settings:
        """Build settings from the environment, with CLI flags taking precedence.

        `overrides` is the CLI's flags; None values mean "not supplied" and fall
        through to the environment, then to the defaults on this class.

        `env` defaults to `read_environment()`: the process environment plus
        `.env`. Pass a mapping to resolve against something else, e.g. in tests.

        Raises:
            ConfigError: if no contact information is available from either source,
                or translation is on without an API key.
        """
        source = read_environment() if env is None else env
        font = source.get(f"{ENV_PREFIX}FONT", "").strip()

        from_env = cls(
            contact=source.get(f"{ENV_PREFIX}CONTACT", "").strip(),
            output_dir=Path(source.get(f"{ENV_PREFIX}OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
            thumbnail_width=_positive_int(
                source, f"{ENV_PREFIX}THUMBNAIL_WIDTH", DEFAULT_THUMBNAIL_WIDTH
            ),
            font_path=Path(font) if font else None,
            quote_provider=source.get(f"{ENV_PREFIX}QUOTE_PROVIDER", "wiktionary"),
            translation_languages=_languages(source, f"{ENV_PREFIX}TRANSLATION_LANGUAGES"),
            google_translate_api_key=(
                source.get(f"{ENV_PREFIX}GOOGLE_TRANSLATE_API_KEY", "").strip() or None
            ),
        )

        # Both checks run on the merged result, so a flag can still settle them:
        # `--contact`, or `--languages ""` for a run without a key.
        settings = from_env.merged_with(**overrides)
        if not settings.contact.strip():
            raise ConfigError(MISSING_CONTACT_MESSAGE)
        if settings.translation_languages and not settings.google_translate_api_key:
            raise ConfigError(MISSING_API_KEY_MESSAGE)
        return settings


def read_environment(dotenv_path: Path = DOTENV_PATH) -> dict[str, str]:
    """The process environment, with `dotenv_path` filling in whatever it lacks.

    Real environment variables win, so a secret set in CI is never overridden by
    a stray `.env` file. A missing file is fine: CI has none. The file is merged
    into a copy rather than loaded into `os.environ`, so reading settings has no
    side effects on the process.
    """
    values = dotenv_values(dotenv_path)
    # A bare `KEY` line with no `=` parses as None; treat it as absent.
    from_file = {key: value for key, value in values.items() if value is not None}
    return {**from_file, **os.environ}


def parse_languages(raw: str) -> tuple[str, ...]:
    """`"ja, sw,fi"` -> `("ja", "sw", "fi")`. An empty string gives `()`.

    Codes are not validated here: the translation API rejects unknown ones with
    a clear message, and keeping a local list in sync with Google's would be a
    chore for no gain.
    """
    return tuple(code.strip() for code in raw.split(",") if code.strip())


def _languages(source: Mapping[str, str], key: str) -> tuple[str, ...]:
    raw = source.get(key)
    return DEFAULT_TRANSLATION_LANGUAGES if raw is None else parse_languages(raw)


def _positive_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{key} must be positive, got {value}")
    return value
