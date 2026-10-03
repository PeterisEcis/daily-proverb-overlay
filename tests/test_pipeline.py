"""The daily job end to end, offline: fake Commons, fake renderer, real disk."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from daily_proverb_overlay.config import ConfigError, Settings
from daily_proverb_overlay.models import Attribution, PictureOfTheDay, Quote
from daily_proverb_overlay.pipeline import (
    PotdOverlayPipeline,
    _suffix_for,
    _translation_chain,
)
from daily_proverb_overlay.render.compositor import RenderedOverlay
from daily_proverb_overlay.sources.quotes import LoremIpsumProvider
from daily_proverb_overlay.sources.translation import TranslationChain, TranslationError
from daily_proverb_overlay.storage import OutputStore
from fakes import FakeHttp, UnsaveableImage

DAY = date(2026, 10, 2)
THUMBNAIL = (
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Sunset.jpg/1920px-Sunset.jpg"
    "?utm_source=commons.wikimedia.org&utm_content=thumbnail"
)
PICTURE = PictureOfTheDay(
    potd_date=DAY,
    file_title="File:Sunset.jpg",
    image_url=THUMBNAIL,
    width=1600,
    height=1200,
    mime="image/jpeg",
    attribution=Attribution(
        file_page_url="https://commons.wikimedia.org/wiki/File:Sunset.jpg",
        title="Sunset",
        artist="Ann Example",
        license_short_name="CC BY-SA 4.0",
    ),
)


class FakePotdClient:
    def __init__(self, picture: PictureOfTheDay) -> None:
        self.picture = picture
        self.calls = 0

    def fetch(self, potd_date: date) -> PictureOfTheDay:
        self.calls += 1
        return replace(self.picture, potd_date=potd_date)


class FakeCompositor:
    """Skips the drawing -- rendering is out of scope here -- but returns a
    real image, so the store has something to save."""

    def __init__(self, image: Any = None) -> None:
        self.image = image if image is not None else Image.new("RGB", (40, 30))

    def render(self, image_path: Path, quote: Quote, credit_line: str) -> RenderedOverlay:
        return RenderedOverlay(
            image=self.image,
            quote_lines=(quote.text,),
            credit_line=credit_line,
            font_path=Path("Fake.ttf"),
            quote_font_size=12,
        )


class EchoTranslator:
    name = "echo"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def translate(self, text: str, *, source: str, target: str) -> str:
        if self.fail:
            raise TranslationError("quota exceeded")
        return f"{text} >{target}"


def make_pipeline(
    root: Path,
    *,
    picture: PictureOfTheDay = PICTURE,
    compositor: FakeCompositor | None = None,
    translation_chain: TranslationChain | None = None,
) -> Any:
    return PotdOverlayPipeline(
        http=FakeHttp(),
        potd_client=FakePotdClient(picture),
        quote_provider=LoremIpsumProvider(),
        compositor=compositor or FakeCompositor(),
        store=OutputStore(root),
        translation_chain=translation_chain,
    )


def read_metadata(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return metadata


def test_first_run_writes_every_published_file(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)

    result = pipeline.run(DAY)

    assert not result.skipped
    assert pipeline.store.is_complete(result.paths)
    assert pipeline.http.downloads == [THUMBNAIL]
    assert "CC BY-SA 4.0" in result.paths.attribution.read_text(encoding="utf-8")

    metadata = read_metadata(result.paths.metadata)
    assert metadata["date"] == "2026-10-02"
    assert metadata["quote"]["provider"] == "lorem"
    assert metadata["attribution"]["share_alike"] is True
    assert (metadata["render"]["width"], metadata["render"]["height"]) == (40, 30)


def test_finished_day_is_skipped_without_touching_the_network(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)
    pipeline.run(DAY)

    result = pipeline.run(DAY)

    assert result.skipped
    assert pipeline.potd_client.calls == 1
    assert len(pipeline.http.downloads) == 1


def test_force_rebuilds_and_downloads_again(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)
    pipeline.run(DAY)

    result = pipeline.run(DAY, force=True)

    assert not result.skipped
    assert pipeline.potd_client.calls == 2
    assert len(pipeline.http.downloads) == 2


def test_rerender_reuses_the_cached_source_image(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)
    first = pipeline.run(DAY)
    first.paths.final_image.unlink()

    second = pipeline.run(DAY)

    assert not second.skipped
    assert pipeline.store.is_complete(second.paths)
    assert len(pipeline.http.downloads) == 1


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)

    result = pipeline.run(DAY, dry_run=True)

    assert result.quote is not None
    assert list(tmp_path.iterdir()) == []
    assert pipeline.http.downloads == []


def test_crash_while_saving_the_image_leaves_the_day_unfinished(tmp_path: Path) -> None:
    crashing = make_pipeline(tmp_path, compositor=FakeCompositor(UnsaveableImage()))

    with pytest.raises(OSError):
        crashing.run(DAY)

    paths = crashing.store.paths_for(DAY)
    # The sidecar files made it out, but the image is written last on purpose.
    assert paths.attribution.exists()
    assert not crashing.store.is_complete(paths)

    # So the next run picks the day up again instead of skipping it.
    retry = make_pipeline(tmp_path)
    assert not retry.run(DAY).skipped
    assert retry.store.is_complete(paths)


def test_failed_translation_writes_nothing(tmp_path: Path) -> None:
    chain = TranslationChain(EchoTranslator(fail=True), ["ja"])
    pipeline = make_pipeline(tmp_path, translation_chain=chain)

    with pytest.raises(TranslationError):
        pipeline.run(DAY)

    assert list(tmp_path.iterdir()) == []


def test_translation_is_recorded_in_the_metadata(tmp_path: Path) -> None:
    chain = TranslationChain(EchoTranslator(), ["ja", "sw"])

    result = make_pipeline(tmp_path, translation_chain=chain).run(DAY)

    quote = read_metadata(result.paths.metadata)["quote"]
    assert quote["text"] == f"{quote['original_text']} >ja >sw >en"
    assert [step["language"] for step in quote["translations"]] == ["ja", "sw", "en"]


def test_cached_source_is_named_after_the_served_thumbnail_not_the_original(
    tmp_path: Path,
) -> None:
    # A TIFF original: Commons serves its thumbnail as a JPEG.
    tiff = replace(
        PICTURE,
        file_title="File:Scan.tif",
        mime="image/tiff",
        image_url="https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Scan.tif/"
        "lossy-page1-1920px-Scan.tif.jpg?utm_source=commons.wikimedia.org",
    )

    result = make_pipeline(tmp_path, picture=tiff).run(DAY)

    assert result.paths.source_image.name == "source.jpg"
    assert result.paths.source_image.is_file()


@pytest.mark.parametrize(
    ("image_url", "suffix"),
    [
        pytest.param(THUMBNAIL, ".jpg", id="query string ignored"),
        pytest.param(".../Scan.tif/lossy-page1-1920px-Scan.tif.jpg", ".jpg", id="tiff thumbnail"),
        pytest.param(".../Map.svg/1920px-Map.svg.png", ".png", id="svg thumbnail"),
        pytest.param(".../Photo.JPG/1920px-Photo.JPG", ".jpg", id="upper case"),
        pytest.param("https://upload.wikimedia.org/a/ab/Scan.tif", ".tif", id="original"),
        pytest.param("https://example.org/image", ".jpg", id="no extension"),
    ],
)
def test_source_suffix_comes_from_the_downloaded_url(image_url: str, suffix: str) -> None:
    assert _suffix_for(replace(PICTURE, image_url=image_url)) == suffix


def test_chain_without_an_api_key_is_refused() -> None:
    # Settings.resolve() stops this first. Settings built by hand still cannot
    # slip an untranslated day through.
    settings = Settings(contact="me@example.org", translation_languages=("ja",))

    with pytest.raises(ConfigError, match="POTD_GOOGLE_TRANSLATE_API_KEY"):
        _translation_chain(settings, FakeHttp())


def test_empty_language_list_turns_translation_off_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(contact="me@example.org", translation_languages=())

    with caplog.at_level(logging.WARNING):
        chain = _translation_chain(settings, FakeHttp())

    assert chain is None
    assert caplog.text == ""


def test_configured_key_builds_the_chain() -> None:
    settings = Settings(
        contact="me@example.org",
        translation_languages=("ja", "sw"),
        google_translate_api_key="secret-key",
    )

    chain = _translation_chain(settings, FakeHttp())

    assert chain is not None
    assert chain.route == "en -> ja -> sw -> en"
