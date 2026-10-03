"""Credit text: what the license obliges every published image to carry."""

from __future__ import annotations

import pytest

from daily_proverb_overlay.attribution import (
    CREDIT_LINE_MAX_CHARS,
    DERIVATIVE_NOTICE,
    SHARE_ALIKE_NOTICE,
    UNKNOWN_AUTHOR,
    UNKNOWN_LICENSE,
    UNKNOWN_TITLE,
    AttributionFormatter,
)
from daily_proverb_overlay.models import Attribution

FILE_PAGE = "https://commons.wikimedia.org/wiki/File:Sunset.jpg"
BY_SA_URL = "https://creativecommons.org/licenses/by-sa/4.0"


def formatter(**fields: str | None) -> AttributionFormatter:
    return AttributionFormatter(Attribution(file_page_url=FILE_PAGE, **fields))


@pytest.mark.parametrize(
    ("license_name", "share_alike"),
    [
        ("CC BY-SA 4.0", True),
        ("CC BY-SA 2.0 de", True),
        ("cc-by-sa-3.0", True),
        ("GFDL", True),
        ("CC BY 4.0", False),
        ("CC0", False),
        ("Public domain", False),
        (None, False),
    ],
)
def test_share_alike_detection(license_name: str | None, share_alike: bool) -> None:
    assert formatter(license_short_name=license_name).is_share_alike is share_alike


def test_credit_line_carries_title_author_and_license() -> None:
    credit = formatter(title="Sunset", artist="Ann Example", license_short_name="CC BY 4.0")

    assert credit.credit_line() == "Sunset · Ann Example · CC BY 4.0"


def test_long_title_is_shortened_but_author_and_license_are_kept_whole() -> None:
    credit = formatter(
        title="A very long title " * 10, artist="Ann Example", license_short_name="CC BY-SA 4.0"
    )

    line = credit.credit_line()

    assert len(line) <= CREDIT_LINE_MAX_CHARS
    assert line.startswith("A very long title")
    assert line.endswith("… · Ann Example · CC BY-SA 4.0")


def test_missing_metadata_falls_back_to_placeholders() -> None:
    assert formatter().credit_line() == f"{UNKNOWN_TITLE} · {UNKNOWN_AUTHOR} · {UNKNOWN_LICENSE}"


def test_full_text_links_the_license_and_states_share_alike() -> None:
    text = formatter(
        title="Sunset",
        artist="Ann Example",
        license_short_name="CC BY-SA 4.0",
        license_url=BY_SA_URL,
    ).full_text()

    assert "Title:   Sunset" in text
    assert "Author:  Ann Example" in text
    assert f"Source:  {FILE_PAGE}" in text
    assert f"License: CC BY-SA 4.0 ({BY_SA_URL})" in text
    assert text.endswith(SHARE_ALIKE_NOTICE)


def test_full_text_for_a_permissive_license_does_not_claim_share_alike() -> None:
    text = formatter(license_short_name="Public domain").full_text()

    assert "License: Public domain\n" in text
    assert text.endswith(DERIVATIVE_NOTICE)
    assert "ShareAlike" not in text


def test_credit_field_appears_only_when_commons_has_one() -> None:
    assert "Credit:  Own work" in formatter(credit="Own work").full_text()
    assert "Credit:" not in formatter().full_text()


def test_metadata_record_agrees_with_the_rendered_text() -> None:
    credit = formatter(title="Sunset", artist="Ann Example", license_short_name="CC BY-SA 4.0")

    record = credit.to_dict()

    assert record["share_alike"] is True
    assert record["credit_line"] == credit.credit_line()
    assert record["file_page_url"] == FILE_PAGE
