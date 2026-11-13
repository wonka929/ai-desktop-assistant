"""CLI di AI Desktop Assistant."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config
from .paths import config_file, find_llama_server
from .server import ServerError, ServerManager
from .llm import LlamaClient


def _gui(argv: list[str]) -> int:
    from .app import AssistantApplication

    config = Config.load()
    config.ensure_file()
    app = AssistantApplication(config)
    return app.run(argv)


def _print_check(ok: bool, label: str, detail: str = "") -> None:
    marker = "OK  " if ok else "!!  "
    line = f"[{marker}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)


def _llama_version(executable: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout or "") + (result.stderr or "")
    first = output.strip().splitlines()[0] if output.strip() else "versione sconosciuta"
    return result.returncode == 0, first


def _build_number(text: str) -> int | None:
    import re

    match = re.search(r"build\s+(\d+)", text)
    return int(match.group(1)) if match else None


def doctor() -> int:
    config = Config.load()
    print("AI Desktop Assistant — diagnosi\n")

    _print_check(True, "Configurazione", str(config.path or config_file()))

    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401

        _print_check(True, "GTK4 / libadwaita", "disponibili")
    except Exception as exc:  # noqa: BLE001
        _print_check(False, "GTK4 / libadwaita", str(exc))

    player = shutil.which(config.tts.player) or shutil.which("pw-play")
    _print_check(bool(player), "Riproduttore audio", player or "pw-play non trovato")

    executable = find_llama_server()
    if executable is None:
        _print_check(False, "llama-server", "non trovato")
    else:
        ok, version = _llama_version(executable)
        build = _build_number(version)
        _print_check(
            ok and (build is None or build >= 11960),
            "llama-server",
            f"{executable} — {version}",
        )
        if build is not None and build < 11960:
            _print_check(
                False,
                "Versione per LFM3",
                f"build {build}: serve b11960+. Esegui scripts/install.sh",
            )

    client = LlamaClient(config.server.base_url, config.llm.model)
    manager = ServerManager(config.server, client)
    unit = manager.unit_exists()
    _print_check(unit, "Servizio systemd", config.server.unit)

    state = manager.probe()
    if state.get("up"):
        sleeping = " (in pausa)" if state.get("sleeping") else ""
        _print_check(True, "llama-server", f"attivo{sleeping}")
    else:
        _print_check(False, "llama-server", "non attivo")

    cache = Path.home() / ".cache/llama.cpp"
    models = sorted(cache.glob("*LFM3*")) if cache.is_dir() else []
    hf_cache = Path.home() / ".cache/huggingface/hub"
    if hf_cache.is_dir():
        models.extend(sorted(hf_cache.glob("models--*LFM3*")))
    _print_check(
        bool(models),
        "Modello in cache",
        ", ".join(item.name for item in models) if models else "verra' scaricato al primo avvio",
    )

    try:
        from .hotkeys import status

        for name, binding, installed in status(config):
            _print_check(installed, name, binding)
    except Exception as exc:  # noqa: BLE001
        _print_check(False, "Scorciatoie GNOME", str(exc))

    print("\nSuggerimenti:")
    print("  ai-assistant server-start   # avvia llama-server")
    print("  ai-assistant toggle         # apre il launcher")
    print("  ai-assistant speak          # legge gli appunti")
    return 0


def setup() -> int:
    config = Config.load()
    path = config.ensure_file()
    print(f"Configurazione: {path}")
    print("Prossimi passi:")
    print("  1. scripts/install.sh          # venv, llama.cpp, servizio, scorciatoie")
    print("  2. systemctl --user start ai-assistant-llama.service")
    print("  3. ai-assistant toggle")
    print("\nIl modello viene scaricato da llama-server al primo avvio (-hf).")
    return 0


def server_start() -> int:
    config = Config.load()
    client = LlamaClient(config.server.base_url, config.llm.model)
    manager = ServerManager(config.server, client)

    def status(state: str) -> None:
        labels = {
            "starting": "Avvio llama-server…",
            "sleeping": "Server in pausa (si ricarica alla richiesta)",
            "ready": "Server pronto",
        }
        print(labels.get(state, state))

    try:
        manager.ensure_ready(on_status=status)
    except ServerError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1
    return 0


def server_stop() -> int:
    config = Config.load()
    client = LlamaClient(config.server.base_url, config.llm.model)
    ServerManager(config.server, client).stop_unit()
    print("Servizio arrestato.")
    return 0


def server_status() -> int:
    config = Config.load()
    client = LlamaClient(config.server.base_url, config.llm.model)
    state = ServerManager(config.server, client).probe()
    if not state.get("up"):
        print("llama-server non attivo.")
        return 1
    print("llama-server attivo" + (" (in pausa)" if state.get("sleeping") else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    command = argv[1] if len(argv) > 1 else "run"

    if command in {"run", "toggle", "speak", "speak-clipboard", "ask"}:
        return _gui(argv)

    parser = argparse.ArgumentParser(prog="ai-assistant")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="diagnostica installazione e server")
    sub.add_parser("setup", help="crea la configurazione e mostra i passi")
    sub.add_parser("server-start", help="avvia llama-server")
    sub.add_parser("server-stop", help="ferma llama-server")
    sub.add_parser("server-status", help="stato di llama-server")
    sub.add_parser("install-shortcuts", help="installa le scorciatoie GNOME")
    sub.add_parser("uninstall-shortcuts", help="rimuove le scorciatoie GNOME")
    sub.add_parser("version", help="versione")

    args = parser.parse_args(argv[1:])

    if args.command == "doctor":
        return doctor()
    if args.command == "setup":
        return setup()
    if args.command == "server-start":
        return server_start()
    if args.command == "server-stop":
        return server_stop()
    if args.command == "server-status":
        return server_status()
    if args.command == "install-shortcuts":
        from .hotkeys import install_from_config

        install_from_config(Config.load())
        return 0
    if args.command == "uninstall-shortcuts":
        from .hotkeys import uninstall

        uninstall()
        print("Scorciatoie rimosse.")
        return 0
    if args.command == "version":
        from . import __version__

        print(__version__)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
