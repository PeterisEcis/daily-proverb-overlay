"""Stages that fetch things from outside the process."""

from __future__ import annotations

from daily_proverb_overlay.sources.quotes import (
    LoremIpsumProvider,
    QuoteProvider,
    available_providers,
    get_quote_provider,
)
from daily_proverb_overlay.sources.wikimedia import (
    CommonsPotdClient,
    PictureOfTheDayNotFound,
    WikimediaError,
)

__all__ = [
    "CommonsPotdClient",
    "LoremIpsumProvider",
    "PictureOfTheDayNotFound",
    "QuoteProvider",
    "WikimediaError",
    "available_providers",
    "get_quote_provider",
]
