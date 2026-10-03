"""The translation chain, and the Google client underneath it."""

from __future__ import annotations

from typing import Any

import pytest

from daily_proverb_overlay.models import Quote
from daily_proverb_overlay.sources.translation import (
    GoogleCloudTranslator,
    TranslationChain,
    TranslationError,
)
from fakes import FakeHttp

PROVERB = Quote(
    text="Haste makes waste",
    source="https://en.wiktionary.org/wiki/haste_makes_waste",
    provider="wiktionary",
)


class TaggingTranslator:
    """Appends each target language to the text, so every hop stays visible."""

    name = "tagging"

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.hops: list[tuple[str, str]] = []

    def translate(self, text: str, *, source: str, target: str) -> str:
        self.hops.append((source, target))
        if target == self.fail_on:
            raise TranslationError(f"no route into {target}")
        return f"{text} >{target}"


def translated(text: str) -> dict[str, Any]:
    return {"data": {"translations": [{"translatedText": text}]}}


def test_chain_goes_out_through_each_language_and_back_home() -> None:
    translator = TaggingTranslator()

    quote = TranslationChain(translator, ["ja", "sw"]).apply(PROVERB)

    assert translator.hops == [("en", "ja"), ("ja", "sw"), ("sw", "en")]
    assert quote.text == "Haste makes waste >ja >sw >en"
    assert quote.original_text == "Haste makes waste"
    assert [step.language for step in quote.translations] == ["ja", "sw", "en"]
    assert quote.translations[-1].text == quote.text
    # Everything that is not the text survives the trip.
    assert (quote.source, quote.provider) == (PROVERB.source, PROVERB.provider)


def test_route_reads_as_the_full_round_trip() -> None:
    assert TranslationChain(TaggingTranslator(), ["ja", "sw"]).route == "en -> ja -> sw -> en"


def test_a_failed_hop_fails_the_chain_instead_of_publishing_untranslated() -> None:
    chain = TranslationChain(TaggingTranslator(fail_on="sw"), ["ja", "sw", "fi"])

    with pytest.raises(TranslationError):
        chain.apply(PROVERB)


def test_chain_needs_at_least_one_language() -> None:
    with pytest.raises(ValueError, match="at least one language"):
        TranslationChain(TaggingTranslator(), [])


def test_api_key_travels_in_a_header_never_in_the_url() -> None:
    http = FakeHttp(translated(" Hola "))

    result = GoogleCloudTranslator(http, "secret-key").translate("Hello", source="en", target="es")

    assert result == "Hola"
    [request] = http.requests
    assert request.headers == {"X-goog-api-key": "secret-key"}
    assert "secret-key" not in repr(request.params)
    assert "secret-key" not in request.url
    assert request.params == {"q": "Hello", "source": "en", "target": "es", "format": "text"}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="no data"),
        pytest.param({"data": None}, id="null data"),
        pytest.param({"data": {"translations": []}}, id="no translations"),
        pytest.param(translated("   "), id="blank"),
        pytest.param({"data": {"translations": [{"translatedText": 42}]}}, id="not text"),
    ],
)
def test_unusable_response_is_a_translation_error(payload: dict[str, Any]) -> None:
    translator = GoogleCloudTranslator(FakeHttp(payload), "secret-key")

    with pytest.raises(TranslationError):
        translator.translate("Hello", source="en", target="es")


def test_blank_api_key_is_rejected_up_front() -> None:
    with pytest.raises(ValueError, match="api_key"):
        GoogleCloudTranslator(FakeHttp(), "   ")
