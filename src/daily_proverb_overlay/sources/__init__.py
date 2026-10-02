"""Stages that fetch things from outside the process."""

from __future__ import annotations

from daily_proverb_overlay.sources.quotes import (
    LoremIpsumProvider,
    QuoteProvider,
    available_providers,
    get_quote_provider,
)
from daily_proverb_overlay.sources.translation import (
    GoogleCloudTranslator,
    TranslationChain,
    TranslationError,
    Translator,
)
from daily_proverb_overlay.sources.wikimedia import (
    CommonsPotdClient,
    PictureOfTheDayNotFound,
    WikimediaError,
)
from daily_proverb_overlay.sources.wiktionary import WiktionaryError, WiktionaryProverbProvider

__all__ = [
    "CommonsPotdClient",
    "GoogleCloudTranslator",
    "LoremIpsumProvider",
    "PictureOfTheDayNotFound",
    "QuoteProvider",
    "TranslationChain",
    "TranslationError",
    "Translator",
    "WikimediaError",
    "WiktionaryError",
    "WiktionaryProverbProvider",
    "available_providers",
    "get_quote_provider",
]
