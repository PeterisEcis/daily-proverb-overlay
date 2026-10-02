"""English proverbs from Wiktionary.

Wiktionary files every proverb entry under `Category:English proverbs`, and the
entry's page title *is* the proverb -- "a bad workman always blames his tools".
So the category listing alone is the whole dataset: no page content is fetched,
and none of the definition text (which is CC BY-SA) ends up in the output.

The listing comes from the same MediaWiki Action API as the Commons lookup, so
the same User-Agent policy applies. It is paged at 500 titles per request, which
for ~1,600 proverbs means four requests a day.

Short proverbs are filtered out after listing. "No pain, no gain" gives the
translation chain little to work with, while a longer sentence has more words
to drift. Five words keeps about 1,270 of the ~1,600.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import date
from typing import Any
from urllib.parse import quote as url_quote

from daily_proverb_overlay.http_client import HttpClient
from daily_proverb_overlay.models import Quote

log = logging.getLogger(__name__)

API_ENDPOINT = "https://en.wiktionary.org/w/api.php"
PAGE_URL = "https://en.wiktionary.org/wiki/{}"
DEFAULT_CATEGORY = "Category:English proverbs"

MAX_LISTING_PAGES = 50
"""Guard against a continuation loop that never ends: 25,000 titles is plenty."""

DEFAULT_MIN_WORDS = 5
"""Shortest proverb worth translating. Words are split on whitespace, so
"don't" and "well-begun" count as one each."""


class WiktionaryError(RuntimeError):
    """The API answered, but not with something usable."""


class WiktionaryProverbProvider:
    """Picks one proverb per day from a Wiktionary category."""

    name = "wiktionary"

    def __init__(
        self,
        http: HttpClient,
        *,
        category: str = DEFAULT_CATEGORY,
        endpoint: str = API_ENDPOINT,
        min_words: int = DEFAULT_MIN_WORDS,
    ) -> None:
        self.http = http
        self.category = category
        self.endpoint = endpoint
        self.min_words = min_words

    def fetch(self, for_date: date) -> Quote:
        listed = self._list_titles()
        titles = [title for title in listed if len(title.split()) >= self.min_words]
        if not titles:
            raise WiktionaryError(
                f"none of the {len(listed)} pages in {self.category} "
                f"has {self.min_words} or more words"
            )

        title = pick_for_date(titles, for_date)
        log.info(
            "proverb for %s: %r (picked from %d of %d with %d+ words)",
            for_date,
            title,
            len(titles),
            len(listed),
            self.min_words,
        )

        return Quote(
            text=_sentence_case(title),
            author=None,
            source=PAGE_URL.format(url_quote(title.replace(" ", "_"))),
            provider=self.name,
        )

    def _list_titles(self) -> list[str]:
        params: dict[str, Any] = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "list": "categorymembers",
            "cmtitle": self.category,
            # Main namespace only: the category also holds an Appendix page.
            "cmnamespace": "0",
            "cmtype": "page",
            "cmlimit": "max",
        }
        titles: list[str] = []

        for _ in range(MAX_LISTING_PAGES):
            payload = self.http.get_json(self.endpoint, params)
            if "error" in payload:
                detail = payload["error"].get("info", payload["error"])
                raise WiktionaryError(f"Wiktionary API error for {self.category}: {detail}")

            members = payload.get("query", {}).get("categorymembers", [])
            titles.extend(str(member["title"]) for member in members if member.get("title"))

            if "continue" not in payload:
                break
            params.update(payload["continue"])
        else:
            raise WiktionaryError(
                f"{self.category} still had more results after {MAX_LISTING_PAGES} pages"
            )

        if not titles:
            raise WiktionaryError(f"{self.category} has no pages")
        return titles


def pick_for_date(titles: Sequence[str], for_date: date) -> str:
    """Choose the day's title, deterministically.

    The titles are put in a fixed shuffled order -- sorted by a hash of the
    title, so it does not depend on how the API happens to sort them -- and the
    date walks that order one step per day. Nothing repeats until the whole list
    has been used, about three and a half years at the current size. When
    Wiktionary adds or removes an entry the list length changes and the walk
    resumes from a new position, which can bring back an earlier proverb; that
    is the price of not keeping a history of what was already used.
    """
    ordered = sorted(set(titles), key=lambda title: hashlib.sha256(title.encode()).digest())
    return ordered[for_date.toordinal() % len(ordered)]


def _sentence_case(title: str) -> str:
    """`don't look a gift horse in the mouth` -> `Don't look a gift horse in the mouth`.

    Only the first character changes: the rest of a title already carries its
    proper nouns ("Rome wasn't built in a day").
    """
    return title[:1].upper() + title[1:]
