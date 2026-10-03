"""Settings precedence: CLI flags, then environment, then `.env`, then defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_proverb_overlay import __version__
from daily_proverb_overlay.config import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TRANSLATION_LANGUAGES,
    ConfigError,
    Settings,
    read_environment,
)

CONTACT = "https://github.com/example/daily-proverb-overlay"
# Translation is on by default, so a complete setup needs the key as well.
KEY = {"POTD_GOOGLE_TRANSLATE_API_KEY": "test-key"}
ENV = {"POTD_CONTACT": CONTACT, **KEY}


@pytest.mark.parametrize("contact", [None, "", "   "])
def test_contact_is_required(contact: str | None) -> None:
    env = {} if contact is None else {"POTD_CONTACT": contact}

    with pytest.raises(ConfigError, match="POTD_CONTACT"):
        Settings.resolve(env=env)


def test_contact_can_come_from_the_command_line_alone() -> None:
    assert Settings.resolve(env=KEY, contact=CONTACT).contact == CONTACT


def test_environment_values_are_read() -> None:
    settings = Settings.resolve(
        env={
            **ENV,
            "POTD_OUTPUT_DIR": "elsewhere",
            "POTD_THUMBNAIL_WIDTH": "800",
            "POTD_QUOTE_PROVIDER": "lorem",
            "POTD_FONT": "fonts/Example.ttf",
        }
    )

    assert settings.output_dir == Path("elsewhere")
    assert settings.thumbnail_width == 800
    assert settings.quote_provider == "lorem"
    assert settings.font_path == Path("fonts/Example.ttf")


def test_flags_beat_the_environment_and_unset_flags_fall_through() -> None:
    settings = Settings.resolve(
        env={**ENV, "POTD_THUMBNAIL_WIDTH": "800", "POTD_QUOTE_PROVIDER": "lorem"},
        thumbnail_width=1200,
        quote_provider=None,
        output_dir=None,
    )

    assert settings.thumbnail_width == 1200
    assert settings.quote_provider == "lorem"
    assert settings.output_dir == DEFAULT_OUTPUT_DIR


@pytest.mark.parametrize(
    ("raw", "languages"),
    [
        pytest.param(None, DEFAULT_TRANSLATION_LANGUAGES, id="unset keeps the default chain"),
        pytest.param("", (), id="empty turns translation off"),
        pytest.param("ja, sw,,fi ", ("ja", "sw", "fi"), id="messy list is tidied"),
    ],
)
def test_translation_languages_from_the_environment(
    raw: str | None, languages: tuple[str, ...]
) -> None:
    env = ENV if raw is None else {**ENV, "POTD_TRANSLATION_LANGUAGES": raw}

    assert Settings.resolve(env=env).translation_languages == languages


def test_empty_languages_flag_overrides_the_environment() -> None:
    # `--languages ""` parses to an empty tuple: falsy, but supplied.
    env = {**ENV, "POTD_TRANSLATION_LANGUAGES": "ja,sw"}

    assert Settings.resolve(env=env, translation_languages=()).translation_languages == ()


@pytest.mark.parametrize("width", ["wide", "0", "-5"])
def test_thumbnail_width_must_be_a_positive_integer(width: str) -> None:
    with pytest.raises(ConfigError, match="POTD_THUMBNAIL_WIDTH"):
        Settings.resolve(env={**ENV, "POTD_THUMBNAIL_WIDTH": width})


def test_api_key_is_trimmed_and_kept_out_of_repr() -> None:
    settings = Settings.resolve(env={**ENV, "POTD_GOOGLE_TRANSLATE_API_KEY": "  secret-key  "})

    assert settings.google_translate_api_key == "secret-key"
    assert "secret-key" not in repr(settings)


@pytest.mark.parametrize("key", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_translation_without_an_api_key_is_refused(key: str | None) -> None:
    # An unset CI secret arrives as an empty string, not as a missing variable.
    env = {"POTD_CONTACT": CONTACT}
    if key is not None:
        env["POTD_GOOGLE_TRANSLATE_API_KEY"] = key

    with pytest.raises(ConfigError, match="POTD_GOOGLE_TRANSLATE_API_KEY"):
        Settings.resolve(env=env)


@pytest.mark.parametrize(
    ("env_languages", "flag"),
    [
        pytest.param("", None, id="turned off in the environment"),
        pytest.param(None, (), id='turned off with --languages ""'),
    ],
)
def test_no_key_is_needed_once_translation_is_off(
    env_languages: str | None, flag: tuple[str, ...] | None
) -> None:
    env = {"POTD_CONTACT": CONTACT}
    if env_languages is not None:
        env["POTD_TRANSLATION_LANGUAGES"] = env_languages

    settings = Settings.resolve(env=env, translation_languages=flag)

    assert settings.translation_languages == ()
    assert settings.google_translate_api_key is None


def test_user_agent_names_the_project_and_how_to_reach_its_operator() -> None:
    user_agent = Settings.resolve(env=ENV).user_agent

    assert user_agent == f"DailyProverbOverlay/{__version__} ({CONTACT})"


def test_real_environment_wins_over_the_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "POTD_CONTACT=from-file\nPOTD_OUTPUT_DIR=file-dir\nPOTD_BARE_KEY\n", encoding="utf-8"
    )
    monkeypatch.setenv("POTD_CONTACT", "from-environment")
    monkeypatch.delenv("POTD_OUTPUT_DIR", raising=False)

    values = read_environment(dotenv)

    assert values["POTD_CONTACT"] == "from-environment"
    assert values["POTD_OUTPUT_DIR"] == "file-dir"
    # A line with no `=` parses as None, which is treated as absent.
    assert "POTD_BARE_KEY" not in values


def test_missing_dotenv_file_is_fine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POTD_CONTACT", "from-environment")

    assert read_environment(tmp_path / "absent.env")["POTD_CONTACT"] == "from-environment"
