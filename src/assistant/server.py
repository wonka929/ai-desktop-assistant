"""Avvio e controllo del servizio llama-server dell'utente."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

from .config import ServerConfig
from .llm import LlamaClient

StatusCallback = Callable[[str], None]


class ServerError(RuntimeError):
    pass


class ServerManager:
    def __init__(self, config: ServerConfig, client: LlamaClient) -> None:
        self.config = config
        self.client = client

    def probe(self) -> dict[str, object]:
        return self.client.probe()

    def unit_exists(self) -> bool:
        result = subprocess.run(
            ["systemctl", "--user", "cat", self.config.unit],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0

    def start_unit(self) -> None:
        result = subprocess.run(
            ["systemctl", "--user", "start", self.config.unit],
            capture_output=True,
            text=True,
            timeout=40,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ServerError(
                f"Non riesco ad avviare {self.config.unit}. "
                f"{detail or 'Controlla con: systemctl --user status ' + self.config.unit}"
            )

    def stop_unit(self) -> None:
        subprocess.run(
            ["systemctl", "--user", "stop", self.config.unit],
            capture_output=True,
            text=True,
            timeout=40,
        )

    def ensure_ready(
        self,
        *,
        timeout: float | None = None,
        on_status: StatusCallback | None = None,
    ) -> None:
        """Avvia il servizio se necessario e attende che il server risponda."""
        timeout = timeout or self.config.startup_timeout
        deadline = time.monotonic() + timeout
        start_requested = False

        while time.monotonic() < deadline:
            state = self.probe()
            if state["up"]:
                if on_status:
                    on_status("sleeping" if state["sleeping"] else "ready")
                return
            if not start_requested:
                if on_status:
                    on_status("starting")
                if not self.unit_exists():
                    raise ServerError(
                        f"Servizio {self.config.unit} non installato. "
                        "Esegui scripts/install.sh oppure ai-assistant setup."
                    )
                self.start_unit()
                start_requested = True
            time.sleep(0.6)

        raise ServerError(
            "llama-server non risponde. Controlla: "
            f"systemctl --user status {self.config.unit}"
        )
