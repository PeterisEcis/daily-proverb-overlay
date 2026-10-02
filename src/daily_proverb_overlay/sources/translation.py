"""Garbling the proverb by translating it through a chain of languages.

The text goes English -> each configured language in turn -> back to English,
and whatever survives is what gets burned onto the image. Every hop loses
something the next language has no way to recover, which is the joke.

Translation goes through the official Google Cloud Translation API (v2, "Basic")
and needs an API key. The free web endpoint that browser extensions use is not
an option: Google's bot filtering rejects `requests` outright and blocks whole
datacenter networks, which is exactly where CI runs.

The key is sent in the `X-goog-api-key` header rather than the `key` query
parameter, so it never appears in a URL -- and therefore never in a logged
request or an exception message.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol, runtime_checkable

from daily_proverb_overlay.http_client import HttpClient
from daily_proverb_overlay.models import Quote, TranslationStep

log = logging.getLogger(__name__)

API_ENDPOINT = "https://translation.googleapis.com/language/translate/v2"


class TranslationError(RuntimeError):
    """The translation API answered, but not with something usable."""


@runtime_checkable
class Translator(Protocol):
    """Translates one piece of text between two languages."""

    name: str

    def translate(self, text: str, *, source: str, target: str) -> str:
        """Return `text`, translated from `source` into `target`."""
        ...


class GoogleCloudTranslator:
    """Google Cloud Translation API v2, authenticated with an API key."""

    name = "google-cloud"

    def __init__(self, http: HttpClient, api_key: str, *, endpoint: str = API_ENDPOINT) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        self.http = http
        self.endpoint = endpoint
        self._api_key = api_key

    def translate(self, text: str, *, source: str, target: str) -> str:
        params = {
            "q": text,
            "source": source,
            "target": target,
            # The default is "html", which returns apostrophes as &#39;.
            "format": "text",
        }
        payload = self.http.get_json(
            self.endpoint, params, headers={"X-goog-api-key": self._api_key}
        )

        try:
            translated = payload["data"]["translations"][0]["translatedText"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError(
                f"unexpected response translating {source} -> {target}: {payload!r}"
            ) from exc

        if not isinstance(translated, str) or not translated.strip():
            raise TranslationError(f"empty translation for {source} -> {target}")
        return translated.strip()


class TranslationChain:
    """Sends a quote out through several languages and back home again."""

    def __init__(
        self,
        translator: Translator,
        languages: Sequence[str],
        *,
        home_language: str = "en",
    ) -> None:
        if not languages:
            raise ValueError("a translation chain needs at least one language")
        self.translator = translator
        self.languages = tuple(languages)
        self.home_language = home_language

    @property
    def route(self) -> str:
        """The chain as a readable string, e.g. `en -> ja -> sw -> en`."""
        return " -> ".join((self.home_language, *self.languages, self.home_language))

    def apply(self, quote: Quote) -> Quote:
        """Return `quote` with its text replaced by the chain's final output.

        Raises:
            TranslationError, HttpError: any hop failed. There is no fallback to
                the untranslated text: a published day is never redone, so a
                silently untranslated image would stay that way.
        """
        text, source = quote.text, self.home_language
        steps: list[TranslationStep] = []

        for target in (*self.languages, self.home_language):
            text = self.translator.translate(text, source=source, target=target)
            steps.append(TranslationStep(language=target, text=text))
            log.debug("%s -> %s: %s", source, target, text)
            source = target

        log.info("translated %s: %r became %r", self.route, quote.text, text)
        return replace(quote, text=text, original_text=quote.text, translations=tuple(steps))
