"""Loads the YAML config (inputs -> destinations) and resolves secrets from env vars.
Nothing secret lives in the YAML: it only names which env var holds each secret."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml

NAME_RE = re.compile(r"[a-z0-9_-]+")


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
    """One Facebook target (a Page id, or `me` for the personal profile)."""

    name: str
    target: str
    token: str = field(repr=False)
    privacy: dict | None = None


@dataclass(frozen=True)
class Input:
    """One church's Restreamer channel and where it fans out to."""

    name: str
    rtmp_name: str
    title: str
    description: str
    destinations: tuple[Destination, ...]


@dataclass(frozen=True)
class Config:
    """Everything Sexton needs to run."""

    core: CoreConfig
    inputs: tuple[Input, ...]
    tick_seconds: float = 10
    grace_seconds: float = 180
    verify_seconds: float = 120
    fb_poll_seconds: float = 30
    ntfy_url: str | None = None
    ntfy_token: str | None = field(default=None, repr=False)
    healthcheck_url: str | None = field(default=None, repr=False)


def _env(name, required=True):
    value = os.environ.get(name)
    if required and not value:
        raise ConfigError(f"environment variable {name} is not set")
    return value or None


def _load_destination(input_name, dest_name, raw):
    if not isinstance(raw, dict) or "target" not in raw or "token_env" not in raw:
        raise ConfigError(f"inputs.{input_name}.destinations.{dest_name} needs target and token_env")
    return Destination(
        name=dest_name,
        target=str(raw["target"]),
        token=_env(raw["token_env"]),
        privacy=raw.get("privacy"),
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

    return Config(
        core=core,
        inputs=inputs,
        tick_seconds=float(raw.get("tick_seconds", 10)),
        grace_seconds=float(raw.get("grace_seconds", 180)),
        verify_seconds=float(raw.get("verify_seconds", 120)),
        fb_poll_seconds=float(raw.get("fb_poll_seconds", 30)),
        ntfy_url=_env("NTFY_URL", required=False),
        ntfy_token=_env("NTFY_TOKEN", required=False),
        healthcheck_url=_env("HEALTHCHECK_URL", required=False),
    )


def validate(inputs):
    """Checks the invariants the reconcile logic relies on."""
    titles = [i.title for i in inputs]
    if len(set(titles)) != len(titles):
        # Titles are how a live video is tied back to its input on a shared
        # target (the profile), so they must be unique.
        raise ConfigError("input titles must be unique")
    for inp in inputs:
        names = [inp.name] + [d.name for d in inp.destinations]
        if not all(NAME_RE.fullmatch(n) for n in names):
            raise ConfigError(f"inputs.{inp.name}: names must match {NAME_RE.pattern} (used in process ids)")
        targets = [d.target for d in inp.destinations]
        if len(set(targets)) != len(targets):
            raise ConfigError(f"inputs.{inp.name} lists the same target twice")
