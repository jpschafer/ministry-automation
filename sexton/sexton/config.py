"""Loads the YAML config (inputs -> destinations) and resolves secrets from env vars.
Nothing secret lives in the YAML: it only names which env var holds each secret."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml

NAME_RE = re.compile(r"[a-z0-9_-]+")
PLATFORMS = ("facebook", "youtube")
YOUTUBE_PRIVACY = ("public", "unlisted", "private")
# Appended to each input's title as "<title> – <date>"; strftime format, local time (TZ).
DEFAULT_TITLE_DATE = "%b %-d, %Y"


class ConfigError(Exception):
    """The config file or environment is missing something required."""


@dataclass(frozen=True)
class CoreConfig:
    """Where the Restreamer Core API lives and how to log in to it."""

    url: str
    username: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class Destination:
    """One place an input goes live: a Facebook Page id, `me` for the personal
    profile, or `mine` for the YouTube channel the refresh token belongs to."""

    name: str
    target: str
    token: str = field(repr=False)
    privacy: dict | str | None = None
    platform: str = "facebook"

    @property
    def slot(self):
        """Identifies the live target across inputs: two destinations with the
        same slot (the profile, the YouTube channel) are shared."""
        return f"{self.platform}:{self.target}"


@dataclass(frozen=True)
class Input:
    """One church's Restreamer channel and where it fans out to."""

    name: str
    rtmp_name: str
    title: str
    description: str
    destinations: tuple[Destination, ...]


@dataclass(frozen=True)
class YouTubeConfig:
    """The Google OAuth client the YouTube refresh tokens were issued to."""

    client_id: str
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class Config:
    """Everything Sexton needs to run."""

    core: CoreConfig
    inputs: tuple[Input, ...]
    tick_seconds: float = 10
    grace_seconds: float = 180
    verify_seconds: float = 120
    poll_seconds: float = 30
    title_date: str | None = DEFAULT_TITLE_DATE
    youtube: YouTubeConfig | None = None
    ntfy_url: str | None = None
    ntfy_token: str | None = field(default=None, repr=False)
    healthcheck_url: str | None = field(default=None, repr=False)


def _env(name, required=True):
    value = os.environ.get(name)
    if required and not value:
        raise ConfigError(f"environment variable {name} is not set")
    return value or None


def _load_destination(input_name, dest_name, raw):
    where = f"inputs.{input_name}.destinations.{dest_name}"
    if not isinstance(raw, dict) or "token_env" not in raw:
        raise ConfigError(f"{where} needs token_env")
    platform = raw.get("platform", "facebook")
    if platform not in PLATFORMS:
        raise ConfigError(f"{where}.platform must be one of {', '.join(PLATFORMS)}")
    if platform == "youtube":
        target = str(raw.get("target", "mine"))
        if target != "mine":
            raise ConfigError(f"{where}.target must be mine (the channel the refresh token belongs to)")
        privacy = raw.get("privacy", "public")
        if privacy not in YOUTUBE_PRIVACY:
            raise ConfigError(f"{where}.privacy must be one of {', '.join(YOUTUBE_PRIVACY)}")
    else:
        if "target" not in raw:
            raise ConfigError(f"{where} needs target")
        target = str(raw["target"])
        privacy = raw.get("privacy")
    return Destination(
        name=dest_name,
        target=target,
        token=_env(raw["token_env"]),
        privacy=privacy,
        platform=platform,
    )


def _load_input(name, raw):
    for key in ("rtmp_name", "title", "destinations"):
        if key not in raw:
            raise ConfigError(f"inputs.{name} is missing {key}")
    destinations = tuple(
        _load_destination(name, dest_name, dest)
        for dest_name, dest in raw["destinations"].items()
    )
    return Input(
        name=name,
        rtmp_name=raw["rtmp_name"],
        title=raw["title"],
        description=raw.get("description", ""),
        destinations=destinations,
    )


def load_config(path):
    """Reads the YAML file at path and resolves every *_env reference."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    core_raw = raw.get("core") or {}
    if "url" not in core_raw:
        raise ConfigError("core.url is required")
    core = CoreConfig(
        url=core_raw["url"].rstrip("/"),
        username=_env(core_raw["username_env"]) if "username_env" in core_raw else None,
        password=_env(core_raw["password_env"]) if "password_env" in core_raw else None,
    )

    inputs = tuple(_load_input(name, value) for name, value in (raw.get("inputs") or {}).items())
    if not inputs:
        raise ConfigError("at least one input is required")
    validate(inputs)

    youtube = None
    if any(d.platform == "youtube" for i in inputs for d in i.destinations):
        yt_raw = raw.get("youtube") or {}
        youtube = YouTubeConfig(
            client_id=_env(yt_raw.get("client_id_env", "YT_CLIENT_ID")),
            client_secret=_env(yt_raw.get("client_secret_env", "YT_CLIENT_SECRET")),
        )

    return Config(
        core=core,
        inputs=inputs,
        tick_seconds=float(raw.get("tick_seconds", 10)),
        grace_seconds=float(raw.get("grace_seconds", 180)),
        verify_seconds=float(raw.get("verify_seconds", 120)),
        # fb_poll_seconds is the pre-YouTube name for the same setting.
        poll_seconds=float(raw.get("poll_seconds", raw.get("fb_poll_seconds", 30))),
        title_date=raw.get("title_date", DEFAULT_TITLE_DATE) or None,
        youtube=youtube,
        ntfy_url=_env("NTFY_URL", required=False),
        ntfy_token=_env("NTFY_TOKEN", required=False),
        healthcheck_url=_env("HEALTHCHECK_URL", required=False),
    )


def validate(inputs):
    """Checks the invariants the reconcile logic relies on."""
    titles = [i.title for i in inputs]
    if len(set(titles)) != len(titles):
        # Titles are how a live video is tied back to its input on a shared
        # target (the profile, the YouTube channel), so they must be unique.
        raise ConfigError("input titles must be unique")
    for a in titles:
        for b in titles:
            if a != b and b.startswith(a + " "):
                # "<title> – <date>" must not be mistaken for another input's title.
                raise ConfigError(f"input title {a!r} is the start of {b!r}; make them distinct")
    for inp in inputs:
        names = [inp.name] + [d.name for d in inp.destinations]
        if not all(NAME_RE.fullmatch(n) for n in names):
            raise ConfigError(f"inputs.{inp.name}: names must match {NAME_RE.pattern} (used in process ids)")
        targets = [d.slot for d in inp.destinations]
        if len(set(targets)) != len(targets):
            raise ConfigError(f"inputs.{inp.name} lists the same target twice")
