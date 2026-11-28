"""Config loading: the shipped example parses, and YouTube settings are validated."""

from __future__ import annotations

import pathlib

import pytest

from sexton.config import ConfigError, load_config

EXAMPLE = pathlib.Path(__file__).resolve().parent.parent / "config.example.yaml"
ENV = ("CORE_USERNAME", "CORE_PASSWORD", "FB_TOKEN_USER", "FB_TOKEN_COLFAX_PAGE", "FB_TOKEN_LEVELS_PAGE",
       "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")


@pytest.fixture(name="env")
def fixture_env(monkeypatch):
    for name in ENV:
        monkeypatch.setenv(name, f"value-of-{name}")


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_example_config_loads(env):  # pylint: disable=unused-argument
    config = load_config(EXAMPLE)
    slots = {d.slot for i in config.inputs for d in i.destinations}
    assert {"facebook:me", "youtube:mine"} <= slots
    assert config.youtube.client_id == "value-of-YT_CLIENT_ID"
    assert config.title_format.startswith("{title} – {day}")
    assert config.poll_seconds == 30


BASE = """
core: {url: http://core}
inputs:
  colfax:
    rtmp_name: a.stream
    title: Colfax
    destinations:
      yt: {platform: youtube, token_env: YT_REFRESH_TOKEN%s}
"""


def test_youtube_defaults_to_public_on_mine(env, tmp_path):  # pylint: disable=unused-argument
    [dest] = load_config(write(tmp_path, BASE % "")).inputs[0].destinations
    assert (dest.platform, dest.target, dest.privacy) == ("youtube", "mine", "public")


@pytest.mark.parametrize("extra", [", privacy: secret", ", target: UCxyz", ", platform: tiktok"])
def test_bad_youtube_settings_are_rejected(env, tmp_path, extra):  # pylint: disable=unused-argument
    text = BASE % extra if "platform" not in extra else BASE.replace("platform: youtube", extra[2:]) % ""
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))


def test_youtube_needs_client_credentials(env, tmp_path, monkeypatch):  # pylint: disable=unused-argument
    monkeypatch.delenv("YT_CLIENT_SECRET")
    with pytest.raises(ConfigError, match="YT_CLIENT_SECRET"):
        load_config(write(tmp_path, BASE % ""))


def test_a_title_that_prefixes_another_is_rejected(env, tmp_path):  # pylint: disable=unused-argument
    text = """
core: {url: http://core}
inputs:
  a: {rtmp_name: a.stream, title: Worship, destinations: {p: {target: me, token_env: FB_TOKEN_USER}}}
  b: {rtmp_name: b.stream, title: Worship Two, destinations: {p: {target: me, token_env: FB_TOKEN_USER}}}
"""
    with pytest.raises(ConfigError, match="start of"):
        load_config(write(tmp_path, text))


@pytest.mark.parametrize(
    "line,expected",
    [("", "{title} – {day}, {year} – {date:%b %-d, %Y}"),
     ('title_format: "{title}"', "{title}"),
     ('title_format: "{title} – {day}"', "{title} – {day}"),
     ('title_date: "%Y-%m-%d"', "{title} – {date:%Y-%m-%d}"),  # older setting still works
     ('title_date: ""', "{title}")],
)
def test_title_format(env, tmp_path, line, expected):  # pylint: disable=unused-argument
    assert load_config(write(tmp_path, line + "\n" + BASE % "")).title_format == expected


@pytest.mark.parametrize("fmt", ["{day} – {title}", "{title} – {sermon}"])
def test_bad_title_format_is_rejected(env, tmp_path, fmt):  # pylint: disable=unused-argument
    with pytest.raises(ConfigError, match="title_format"):
        load_config(write(tmp_path, f'title_format: "{fmt}"\n' + BASE % ""))
