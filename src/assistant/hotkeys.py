"""Scorciatoie globali GNOME tramite gsettings."""

from __future__ import annotations

import shlex
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

from .config import Config  # noqa: E402
from .paths import app_command  # noqa: E402

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
KEYBINDING_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
BASE_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"

LAUNCHER_SLUG = "ai-assistant-launcher"
SPEAK_SLUG = "ai-assistant-speak"


def _settings() -> Gio.Settings:
    return Gio.Settings.new(MEDIA_KEYS)


def _keybinding(path: str) -> Gio.Settings:
    return Gio.Settings.new_with_path(KEYBINDING_SCHEMA, path)


def _paths(settings: Gio.Settings) -> list[str]:
    return list(settings.get_strv("custom-keybindings"))


def _command(action: str) -> str:
    return f"{shlex.quote(app_command())} {action}"


def _entries(config: Config) -> list[tuple[str, str, str, str]]:
    return [
        (
            LAUNCHER_SLUG,
            "AI Assistant: launcher",
            _command("toggle"),
            config.ui.hotkey,
        ),
        (
            SPEAK_SLUG,
            "AI Assistant: leggi appunti",
            _command("speak"),
            config.ui.speak_hotkey,
        ),
    ]


def _conflicts(settings: Gio.Settings, own: set[str]) -> list[str]:
    result: list[str] = []
    for path in _paths(settings):
        if path in own:
            continue
        binding = _keybinding(path).get_string("binding")
        if binding:
            result.append(f"{binding} ({path})")
    return result


def install(config: Config) -> list[str]:
    settings = _settings()
    current = _paths(settings)
    messages: list[str] = []
    own_paths: set[str] = set()

    entries = _entries(config)
    existing_bindings: dict[str, str] = {}
    for path in current:
        if not path.startswith(BASE_PATH):
            continue
        binding = _keybinding(path).get_string("binding")
        if binding:
            existing_bindings[binding] = path

    for slug, name, command, binding in entries:
        path = f"{BASE_PATH}{slug}/"
        own_paths.add(path)
        if path not in current:
            current.append(path)
        sub = _keybinding(path)
        sub.set_string("name", name)
        sub.set_string("command", command)
        sub.set_string("binding", binding)
        messages.append(f"{name}: {binding}")

        other = existing_bindings.get(binding)
        if other and other != path:
            messages.append(
                f"ATTENZIONE: {binding} e' gia' usata da {other}; "
                "scegli un'altra scorciatoia nella configurazione."
            )

    settings.set_strv("custom-keybindings", current)
    Gio.Settings.sync()
    return messages


def uninstall() -> None:
    settings = _settings()
    current = _paths(settings)
    ours = {f"{BASE_PATH}{LAUNCHER_SLUG}/", f"{BASE_PATH}{SPEAK_SLUG}/"}
    kept = [path for path in current if path not in ours]
    settings.set_strv("custom-keybindings", kept)
    Gio.Settings.sync()


def status(config: Config) -> list[tuple[str, str, bool]]:
    settings = _settings()
    current = set(_paths(settings))
    result: list[tuple[str, str, bool]] = []
    for slug, name, _command_text, binding in _entries(config):
        path = f"{BASE_PATH}{slug}/"
        installed = path in current
        actual = _keybinding(path).get_string("binding") if installed else ""
        result.append((name, actual or binding, installed))
    return result


def install_from_config(config: Config) -> None:
    for message in install(config):
        print(message)


def config_path_hint() -> Path:
    from .paths import config_file

    return config_file()
