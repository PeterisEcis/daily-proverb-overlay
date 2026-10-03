"""The GitHub Pages site: what gets published, and that uploader text stays inert."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from daily_proverb_overlay.attribution import DERIVATIVE_NOTICE, SHARE_ALIKE_NOTICE
from daily_proverb_overlay.site_builder import SiteError, build_site


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "output"


@pytest.fixture
def site_dir(tmp_path: Path) -> Path:
    return tmp_path / "_site"


def finish_day(output_dir: Path, day: str, **attribution: Any) -> Path:
    """Write a finished day folder, as the daily job would leave it."""
    directory = output_dir / day
    directory.mkdir(parents=True)
    (directory / "overlay.jpg").write_bytes(b"jpeg bytes")
    (directory / "attribution.txt").write_text("credit\n", encoding="utf-8")
    metadata = {
        "quote": {
            "text": f"Proverb for {day}",
            "original_text": "Haste makes waste",
            "source": "https://en.wiktionary.org/wiki/haste_makes_waste",
            "translations": [{"language": "ja", "text": "急いては事を仕損じる"}],
        },
        "attribution": {
            "title": "Sunset",
            "artist": "Ann Example",
            "license_short_name": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
            "file_page_url": "https://commons.wikimedia.org/wiki/File:Sunset.jpg",
            "share_alike": True,
            **attribution,
        },
        "render": {"width": 1920, "height": 1080},
    }
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return directory


def page(site_dir: Path, day: str) -> str:
    return (site_dir / day / "index.html").read_text(encoding="utf-8")


def test_builds_a_page_per_day_with_the_newest_on_the_front_page(
    output_dir: Path, site_dir: Path
) -> None:
    for day in ("2026-09-30", "2026-10-02", "2026-10-01"):
        finish_day(output_dir, day)

    assert build_site(output_dir, site_dir) == 3

    index = (site_dir / "index.html").read_text(encoding="utf-8")
    assert '<time datetime="2026-10-02">' in index
    assert index.index('href="2026-10-01/"') < index.index('href="2026-09-30/"')
    for day in ("2026-09-30", "2026-10-01", "2026-10-02"):
        assert (site_dir / day / "overlay.jpg").read_bytes() == b"jpeg bytes"
        assert "Proverb for" in page(site_dir, day)


def test_skips_unfinished_and_unreadable_days(output_dir: Path, site_dir: Path) -> None:
    finish_day(output_dir, "2026-10-02")
    (finish_day(output_dir, "2026-10-01") / "overlay.jpg").unlink()
    (finish_day(output_dir, "2026-09-30") / "metadata.json").write_text("{", encoding="utf-8")
    (output_dir / "notes").mkdir()

    assert build_site(output_dir, site_dir) == 1
    assert not (site_dir / "2026-10-01").exists()
    assert not (site_dir / "2026-09-30").exists()


def test_uploader_supplied_markup_is_escaped(output_dir: Path, site_dir: Path) -> None:
    finish_day(output_dir, "2026-10-02", artist="<script>alert(1)</script>")

    build_site(output_dir, site_dir)

    html = page(site_dir, "2026-10-02")
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_non_web_urls_are_shown_as_text_not_links(output_dir: Path, site_dir: Path) -> None:
    finish_day(output_dir, "2026-10-02", license_url="javascript:alert(1)")

    build_site(output_dir, site_dir)

    html = page(site_dir, "2026-10-02")
    assert "javascript:" not in html
    assert "CC BY-SA 4.0" in html


@pytest.mark.parametrize(
    ("share_alike", "notice"), [(True, SHARE_ALIKE_NOTICE), (False, DERIVATIVE_NOTICE)]
)
def test_page_carries_the_matching_license_notice(
    output_dir: Path, site_dir: Path, share_alike: bool, notice: str
) -> None:
    finish_day(output_dir, "2026-10-02", share_alike=share_alike)

    build_site(output_dir, site_dir)

    assert notice in page(site_dir, "2026-10-02")


def test_nothing_to_publish_is_an_error(output_dir: Path, site_dir: Path) -> None:
    output_dir.mkdir()

    with pytest.raises(SiteError, match="nothing to publish"):
        build_site(output_dir, site_dir)


def test_refuses_to_wipe_a_folder_it_did_not_build(output_dir: Path, site_dir: Path) -> None:
    finish_day(output_dir, "2026-10-02")
    site_dir.mkdir()
    (site_dir / "thesis.docx").write_bytes(b"irreplaceable")

    with pytest.raises(SiteError, match="refusing to delete"):
        build_site(output_dir, site_dir)

    assert (site_dir / "thesis.docx").read_bytes() == b"irreplaceable"


def test_rebuild_leaves_nothing_stale(output_dir: Path, site_dir: Path) -> None:
    finish_day(output_dir, "2026-10-02")
    removed = finish_day(output_dir, "2026-10-01")
    build_site(output_dir, site_dir)

    (removed / "overlay.jpg").unlink()
    build_site(output_dir, site_dir)

    assert not (site_dir / "2026-10-01").exists()
    assert (site_dir / "2026-10-02" / "index.html").exists()
