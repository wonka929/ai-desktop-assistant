"""Percorsi XDG e individuazione di llama-server."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_DIR_NAME = "ai-desktop-assistant"
APP_ID = "io.github.wonka.AiDesktopAssistant"


def _xdg(env: str, fallback: str) -> Path:
    value = os.environ.get(env)
    return Path(value).expanduser() if value else Path.home() / fallback


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_DIR_NAME


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / APP_DIR_NAME


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / APP_DIR_NAME


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / APP_DIR_NAME


def config_file() -> Path:
    return config_dir() / "config.toml"


def tts_cache_dir() -> Path:
    return cache_dir() / "tts"


def llama_server_candidates() -> list[Path]:
    """Percorsi in ordine di preferenza.

    La build di sistema puo' essere troppo vecchia per LFM3: la nostra build
    locale, se presente, ha la precedenza.
    """
    candidates: list[Path] = []
    env = os.environ.get("LLAMA_SERVER")
    if env:
        candidates.append(Path(env).expanduser())

    opt = Path.home() / ".local/opt/llama.cpp"
    if opt.is_dir():
        for child in sorted(opt.iterdir(), reverse=True):
            if child.is_dir() and (child / "llama-server").is_file():
                candidates.append(child / "llama-server")

    which = shutil.which("llama-server")
    if which:
        candidates.append(Path(which))
    return candidates


def find_llama_server() -> Path | None:
    for candidate in llama_server_candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def app_command() -> str:
    """Comando assoluto usato nelle scorciatoie GNOME."""
    local = Path.home() / ".local/bin/ai-assistant"
    if local.is_file():
        return str(local)
    which = shutil.which("ai-assistant")
    if which:
        return which
    return "ai-assistant"


def ensure_dirs() -> None:
    for directory in (config_dir(), cache_dir(), state_dir(), data_dir(), tts_cache_dir()):
        directory.mkdir(parents=True, exist_ok=True)
