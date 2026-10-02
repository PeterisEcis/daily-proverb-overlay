"""Where the overlay text comes from.

Only placeholder text for now. `QuoteProvider` exists so that swapping in a real
source later is an additive change: write a class with a `fetch()` method,
register it in `_PROVIDERS`, and the rest of the pipeline is untouched. That
swap is a later job, once the new provider needs an API key and therefore a
secret.

The placeholder is seeded by date rather than random, which matters more than it
looks: rerunning the job for the same day produces identical text, so the
idempotency check in `storage.py` is actually verifiable.
"""

from __future__ import annotations

import logging
import random
from datetime import date
from typing import Protocol, runtime_checkable

from daily_proverb_overlay.models import Quote

log = logging.getLogger(__name__)


@runtime_checkable
class QuoteProvider(Protocol):
    """A source of overlay text.

    Implementations must be deterministic for a given date if the pipeline's
    idempotency guarantee is to mean anything.
    """

    name: str

    def fetch(self, for_date: date) -> Quote:
        """Return the quote to overlay for `for_date`."""
        ...


class LoremIpsumProvider:
    """Filler text, until a real quote source is chosen.

    Deliberately obvious as a placeholder: the author reads `Lorem Ipsum`, and
    `Quote.is_placeholder` is True, so nothing accidentally gets published as
    though it were a real proverb.
    """

    name = "lorem"

    SENTENCES: tuple[str, ...] = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
        "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris.",
        "Duis aute irure dolor in reprehenderit in voluptate velit esse.",
        "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia.",
        "Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit.",
        "Neque porro quisquam est qui dolorem ipsum quia dolor sit amet.",
        "Quis autem vel eum iure reprehenderit qui in ea voluptate velit.",
    )

    def __init__(self, sentence_count: int = 2) -> None:
        if sentence_count < 1:
            raise ValueError("sentence_count must be at least 1")
        self.sentence_count = min(sentence_count, len(self.SENTENCES))

    def fetch(self, for_date: date) -> Quote:
        # Seeding on the date keeps output stable per day while still varying
        # day to day, so repeated runs are byte-identical.
        rng = random.Random(for_date.isoformat())
        sentences = rng.sample(self.SENTENCES, self.sentence_count)

        log.info("using placeholder quote text (provider=%s)", self.name)
        return Quote(
            text=" ".join(sentences),
            author="Lorem Ipsum",
            source=None,
            provider=self.name,
        )


_PROVIDERS: dict[str, type] = {
    LoremIpsumProvider.name: LoremIpsumProvider,
}


def available_providers() -> tuple[str, ...]:
    """Names accepted by `get_quote_provider`, for CLI help and validation."""
    return tuple(sorted(_PROVIDERS))


def get_quote_provider(name: str, **kwargs: object) -> QuoteProvider:
    """Look up a provider by name.

    Raises:
        KeyError: if `name` is not registered.
    """
    try:
        provider_class = _PROVIDERS[name]
    except KeyError:
        known = ", ".join(available_providers())
        raise KeyError(f"unknown quote provider {name!r}; available: {known}") from None
    return provider_class(**kwargs)  # type: ignore[no-any-return]
