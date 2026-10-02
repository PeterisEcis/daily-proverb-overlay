"""Settings for a single run.

Values come from CLI flags first, then environment variables, then the defaults
here. Nothing is read from a config file yet -- that arrives when there is a
secret worth keeping out of the repo.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from daily_proverb_overlay import __version__

ENV_PREFIX = "POTD_"

DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_THUMBNAIL_WIDTH = 1600
"""Raw POTD files are routinely 50-100MB TIFFs. Always ask for a thumbnail."""


MISSING_CONTACT_MESSAGE = (
    "No contact information configured. Wikimedia's User-Agent policy requires a "
    "descriptive agent that includes a way to reach the operator, and blocks "
    "requests that lack one.\n"
    "Set it for the session:\n"
    '  $env:POTD_CONTACT = "https://github.com/you/pipelines"\n'
    "or pass it per run:\n"
    "  --contact https://github.com/you/pipelines"
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
    quote_provider: str = "lorem"

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

        Raises:
            ConfigError: if no contact information is available from either source.
        """
        source = os.environ if env is None else env
        font = source.get(f"{ENV_PREFIX}FONT", "").strip()

        from_env = cls(
            contact=source.get(f"{ENV_PREFIX}CONTACT", "").strip(),
            output_dir=Path(source.get(f"{ENV_PREFIX}OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
            thumbnail_width=_positive_int(
                source, f"{ENV_PREFIX}THUMBNAIL_WIDTH", DEFAULT_THUMBNAIL_WIDTH
            ),
            font_path=Path(font) if font else None,
            quote_provider=source.get(f"{ENV_PREFIX}QUOTE_PROVIDER", "lorem"),
        )

        settings = from_env.merged_with(**overrides)
        if not settings.contact.strip():
            raise ConfigError(MISSING_CONTACT_MESSAGE)
        return settings


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
