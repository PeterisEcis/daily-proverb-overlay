"""Looking up the Picture of the Day, against recorded and hand-built responses."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from daily_proverb_overlay.sources.wikimedia import (
    CommonsPotdClient,
    PictureOfTheDayNotFound,
    WikimediaError,
    html_to_text,
)
from fakes import FakeHttp, load_fixture

RECORDED = "commons_potd_2026-10-02.json"
RECORDED_DATE = date(2026, 10, 2)


def image_page(
    title: str,
    *,
    width: int = 4000,
    height: int = 3000,
    thumbnail: bool = True,
    extmetadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One page of a `generator=images` response, shaped like the real thing."""
    name = title.removeprefix("File:").replace(" ", "_")
    info: dict[str, Any] = {
        "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{name}",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
        "width": width,
        "height": height,
        "mime": "image/jpeg",
        "extmetadata": extmetadata or {},
    }
    if thumbnail:
        info |= {
            "thumburl": f"https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/{name}/1920px-{name}",
            "thumbwidth": 1600,
            "thumbheight": 1200,
        }
    return {"ns": 6, "title": title, "imageinfo": [info]}


def response(*pages: dict[str, Any]) -> dict[str, Any]:
    return {"query": {"pages": list(pages)}}


def fetch(payload: dict[str, Any], potd_date: date = RECORDED_DATE) -> Any:
    return CommonsPotdClient(FakeHttp(payload)).fetch(potd_date)


def test_recorded_response_becomes_a_picture_with_plain_text_credit() -> None:
    picture = fetch(load_fixture(RECORDED))

    assert picture.potd_date == RECORDED_DATE
    assert picture.file_title == "File:Gene Autry, NPG 94 39.jpg"
    assert picture.image_url.startswith("https://thumb.wikimedia.org/")
    assert (picture.width, picture.height) == (1600, 1971)

    credit = picture.attribution
    assert credit.title == "Gene Autry, NPG 94 39"
    # Commons sends Artist as nested <bdi><a><span> markup.
    assert credit.artist == "Harry Warnecke / Robert F. Cranston"
    assert credit.credit == "National Portrait Gallery, Smithsonian Institution"
    assert credit.license_short_name == "Public domain"
    assert credit.license_url is None
    assert credit.file_page_url == (
        "https://commons.wikimedia.org/wiki/File:Gene_Autry,_NPG_94_39.jpg"
    )


def test_asks_for_a_thumbnail_of_the_days_template() -> None:
    http = FakeHttp(load_fixture(RECORDED))

    CommonsPotdClient(http, thumbnail_width=1600).fetch(RECORDED_DATE)

    [request] = http.requests
    assert request.params["titles"] == "Template:Potd/2026-10-02"
    # Without this the API hands back the original, often a 50-100MB TIFF.
    assert request.params["iiurlwidth"] == 1600


def test_picks_the_photo_over_a_license_icon_the_template_also_embeds() -> None:
    icon = image_page("File:Cc-by-sa.svg", width=88, height=31)
    photo = image_page("File:Sunset.jpg", width=4000, height=3000)

    assert fetch(response(icon, photo)).file_title == "File:Sunset.jpg"


def test_falls_back_to_the_original_when_there_is_no_thumbnail() -> None:
    page = image_page("File:Scan.tif", width=5000, height=4000, thumbnail=False)

    picture = fetch(response(page))

    assert picture.image_url == "https://upload.wikimedia.org/wikipedia/commons/a/ab/Scan.tif"
    assert (picture.width, picture.height) == (5000, 4000)


def test_title_comes_from_the_file_name_when_object_name_is_blank() -> None:
    page = image_page("File:Sunset_over_Riga.old.jpg", extmetadata={"ObjectName": {"value": " "}})

    assert fetch(response(page)).attribution.title == "Sunset over Riga.old"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"batchcomplete": True}, id="template does not exist yet"),
        pytest.param(response({"title": "File:Gone.jpg", "missing": True}), id="file deleted"),
    ],
)
def test_no_published_image_is_reported_as_not_found(payload: dict[str, Any]) -> None:
    with pytest.raises(PictureOfTheDayNotFound):
        fetch(payload, date(2030, 1, 1))


def test_api_error_is_reported_with_the_apis_explanation() -> None:
    payload = {"error": {"code": "ratelimited", "info": "You've exceeded your rate limit."}}

    with pytest.raises(WikimediaError, match="exceeded your rate limit") as excinfo:
        fetch(payload)

    # Not "no POTD yet": the CLI gives those two different exit codes.
    assert excinfo.type is WikimediaError


@pytest.mark.parametrize(
    ("fragment", "text"),
    [
        ('<a href="https://example.org">Ann Example</a>', "Ann Example"),
        ("Smith &amp; Jones", "Smith & Jones"),
        ("Line one<br>line two", "Line one line two"),
        ("  lots \n of   space ", "lots of space"),
        ("<span>never closed", "never closed"),
        ("plain text", "plain text"),
    ],
)
def test_html_fragments_flatten_to_plain_text(fragment: str, text: str) -> None:
    assert html_to_text(fragment) == text
