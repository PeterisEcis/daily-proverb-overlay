"""Choosing the day's proverb from the Wiktionary category listing."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

import pytest

from daily_proverb_overlay.sources.wiktionary import (
    MAX_LISTING_PAGES,
    WiktionaryError,
    WiktionaryProverbProvider,
    pick_for_date,
)
from fakes import FakeHttp

DAY = date(2026, 10, 2)
PROVERBS = [f"every dog has its day number {n}" for n in range(30)]


def listing(*titles: str, more: str | None = None) -> dict[str, Any]:
    """One page of a `list=categorymembers` response."""
    payload: dict[str, Any] = {
        "query": {"categorymembers": [{"ns": 0, "title": title} for title in titles]}
    }
    if more is not None:
        payload["continue"] = {"cmcontinue": more, "continue": "-||"}
    return payload


def test_same_date_always_gives_the_same_proverb() -> None:
    assert pick_for_date(PROVERBS, DAY) == pick_for_date(PROVERBS, DAY)


def test_order_the_api_lists_titles_in_does_not_matter() -> None:
    shuffled = PROVERBS.copy()
    random.Random(1).shuffle(shuffled)

    assert pick_for_date(shuffled, DAY) == pick_for_date(PROVERBS, DAY)
    assert pick_for_date(PROVERBS + PROVERBS, DAY) == pick_for_date(PROVERBS, DAY)


def test_every_proverb_is_used_before_any_repeats() -> None:
    days = [DAY + timedelta(days=n) for n in range(len(PROVERBS))]

    assert {pick_for_date(PROVERBS, day) for day in days} == set(PROVERBS)


def test_short_proverbs_are_never_picked() -> None:
    http = FakeHttp(
        listing("no pain no gain", "haste makes waste", "a bad workman always blames his tools")
    )

    quote = WiktionaryProverbProvider(http, min_words=5).fetch(DAY)

    assert quote.text == "A bad workman always blames his tools"


def test_quote_links_back_to_the_wiktionary_entry() -> None:
    quote = WiktionaryProverbProvider(FakeHttp(listing("rome wasn't built in a day"))).fetch(DAY)

    assert quote.text == "Rome wasn't built in a day"
    assert quote.source == "https://en.wiktionary.org/wiki/rome_wasn%27t_built_in_a_day"
    assert quote.author is None
    assert quote.provider == "wiktionary"


def test_follows_continuation_until_the_listing_ends() -> None:
    http = FakeHttp(
        listing("first page proverb with six words", more="page|2"),
        listing("second page proverb with six words"),
    )

    WiktionaryProverbProvider(http).fetch(DAY)

    assert len(http.requests) == 2
    assert "cmcontinue" not in http.requests[0].params
    assert http.requests[1].params["cmcontinue"] == "page|2"


def test_gives_up_on_a_listing_that_never_ends() -> None:
    endless = listing("a proverb that goes on forever", more="again")
    http = FakeHttp(*[endless] * MAX_LISTING_PAGES)

    with pytest.raises(WiktionaryError, match="still had more results"):
        WiktionaryProverbProvider(http).fetch(DAY)


def test_api_error_is_reported() -> None:
    http = FakeHttp({"error": {"code": "badvalue", "info": "Invalid category title."}})

    with pytest.raises(WiktionaryError, match="Invalid category title"):
        WiktionaryProverbProvider(http).fetch(DAY)


def test_category_with_only_short_proverbs_is_an_error() -> None:
    http = FakeHttp(listing("no pain no gain", "haste makes waste"))

    with pytest.raises(WiktionaryError, match="5 or more words"):
        WiktionaryProverbProvider(http, min_words=5).fetch(DAY)
