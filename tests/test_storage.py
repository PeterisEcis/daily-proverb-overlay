"""The idempotency check, and the atomic writes it depends on."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from daily_proverb_overlay.storage import OutputPaths, OutputStore
from fakes import UnsaveableImage

DAY = date(2026, 10, 2)


@pytest.fixture
def store(tmp_path: Path) -> OutputStore:
    return OutputStore(tmp_path)


@pytest.fixture
def paths(store: OutputStore) -> OutputPaths:
    paths = store.paths_for(DAY)
    store.ensure_directory(paths)
    return paths


def finish(paths: OutputPaths) -> None:
    for path in paths.published_files:
        path.write_bytes(b"done")


def test_everything_for_a_day_lives_in_one_dated_folder(store: OutputStore) -> None:
    paths = store.paths_for(DAY, ".png")

    assert paths.directory == store.root / "2026-10-02"
    assert {path.parent for path in (paths.source_image, *paths.published_files)} == {
        paths.directory
    }
    assert paths.source_image.name == "source.png"


def test_day_is_complete_once_every_published_file_exists(
    store: OutputStore, paths: OutputPaths
) -> None:
    assert not store.is_complete(paths)

    finish(paths)

    assert store.is_complete(paths)


def test_cached_source_image_is_not_needed_for_completeness(
    store: OutputStore, paths: OutputPaths
) -> None:
    finish(paths)

    assert not paths.source_image.exists()
    assert store.is_complete(paths)


@pytest.mark.parametrize("index", [0, 1, 2], ids=["image", "attribution", "metadata"])
def test_one_missing_or_empty_file_means_unfinished(
    store: OutputStore, paths: OutputPaths, index: int
) -> None:
    finish(paths)
    victim = paths.published_files[index]

    victim.write_bytes(b"")
    assert not store.is_complete(paths)

    victim.unlink()
    assert not store.is_complete(paths)


def test_writes_land_whole_and_leave_no_temp_files(store: OutputStore, paths: OutputPaths) -> None:
    store.write_text(paths.attribution, "Title:   Sunset\n")
    store.write_json(paths.metadata, {"quote": "疲れた者に安息はない"})
    store.write_image(paths.final_image, Image.new("RGB", (8, 8)))

    assert paths.attribution.read_text(encoding="utf-8") == "Title:   Sunset\n"
    # Kept readable rather than \u-escaped.
    assert "疲れた者に安息はない" in paths.metadata.read_text(encoding="utf-8")
    assert sorted(path.name for path in paths.directory.iterdir()) == [
        "attribution.txt",
        "metadata.json",
        "overlay.jpg",
    ]


def test_a_save_that_dies_midway_leaves_nothing_behind(
    store: OutputStore, paths: OutputPaths
) -> None:
    with pytest.raises(OSError):
        store.write_image(paths.final_image, UnsaveableImage())

    assert list(paths.directory.iterdir()) == []


def test_a_failed_rewrite_keeps_the_previous_file_intact(
    store: OutputStore, paths: OutputPaths
) -> None:
    store.write_image(paths.final_image, Image.new("RGB", (8, 8)))
    before = paths.final_image.read_bytes()

    with pytest.raises(OSError):
        store.write_image(paths.final_image, UnsaveableImage())

    assert paths.final_image.read_bytes() == before


def test_leftovers_from_an_interrupted_run_are_cleaned_up(
    store: OutputStore, paths: OutputPaths
) -> None:
    finish(paths)
    (paths.directory / ".overlay.k2j4.part.jpg").write_bytes(b"half")
    (paths.directory / ".source.jpg.x9q1.part").write_bytes(b"half")

    assert store.clean_partials(paths) == 2
    assert store.is_complete(paths)
    assert not list(paths.directory.glob(".*"))


def test_cleaning_a_day_that_never_started_is_a_no_op(store: OutputStore) -> None:
    assert store.clean_partials(store.paths_for(DAY)) == 0
