"""Configurazione TOML dell'assistente."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

from .paths import config_file

DEFAULT_SYSTEM_PROMPT = (
    "Sei un assistente desktop locale. Rispondi in italiano, in modo chiaro e conciso. "
    "Non inventare informazioni: se non sai qualcosa, dillo. "
    "Non usare markdown complesso, tabelle o blocchi di codice se non sono richiesti."
)

DEFAULT_CONFIG_TOML = """\
# Configurazione di AI Desktop Assistant
# Modifica questo file e riavvia l'app (o usa "Nuova chat").
# Il server si trova in http://127.0.0.1:8090.

[server]
base_url = "http://127.0.0.1:8090"
unit = "ai-assistant-llama.service"
startup_timeout = 120

[ui]
hotkey = "<Control><Alt>space"
speak_hotkey = "<Control><Alt>r"
hide_on_focus_loss = true
language = "it"

[llm]
# Vuoto = usa il primo modello esposto da llama-server.
model = ""
max_tokens = 1024
temperature = 0.0
max_history_messages = 16
system_prompt = \"""" + DEFAULT_SYSTEM_PROMPT + """\"

[tts]
enabled = true
lang = "it"
# "female" oppure "male" per LFM3-Omni.
voice = "female"
# File wav opzionale con un campione di voce (consigliato dal model card).
speaker_ref = ""
chunk_chars = 320
max_chars = 4000
cache = true
player = "pw-play"
"""


@dataclass
class ServerConfig:
    base_url: str = "http://127.0.0.1:8090"
    unit: str = "ai-assistant-llama.service"
    startup_timeout: int = 120


@dataclass
class UIConfig:
    hotkey: str = "<Control><Alt>space"
    speak_hotkey: str = "<Control><Alt>r"
    hide_on_focus_loss: bool = True
    language: str = "it"


@dataclass
class LLMConfig:
    model: str = ""
    max_tokens: int = 1024
    temperature: float = 0.0
    max_history_messages: int = 16
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


@dataclass
class TTSConfig:
    enabled: bool = True
    lang: str = "it"
    voice: str = "female"
    speaker_ref: str = ""
    chunk_chars: int = 320
    max_chars: int = 4000
    cache: bool = True
    player: str = "pw-play"


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_file()
        data: dict[str, Any] = {}
        if path.is_file():
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        config = cls(
            server=_build(ServerConfig, data.get("server", {})),
            ui=_build(UIConfig, data.get("ui", {})),
            llm=_build(LLMConfig, data.get("llm", {})),
            tts=_build(TTSConfig, data.get("tts", {})),
            path=path,
        )
        config.server.base_url = config.server.base_url.rstrip("/")
        return config

    def ensure_file(self) -> Path:
        path = self.path or config_file()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        return path


T = TypeVar("T")


def _build(cls: type[T], values: dict[str, Any]) -> T:
    allowed = {item.name for item in fields(cls)}
    clean = {key: value for key, value in values.items() if key in allowed}
    return cls(**clean)  # type: ignore[arg-type]
