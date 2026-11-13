"""Client HTTP per llama-server: chat in streaming e TTS."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx

DeltaCallback = Callable[[str], None]
DoneCallback = Callable[[str], None]
ErrorCallback = Callable[[Exception], None]
StatusCallback = Callable[[str], None]


class LlamaError(RuntimeError):
    pass


class LlamaClient:
    def __init__(self, base_url: str, model: str = "", timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.configured_model = model
        self._model = model
        self._timeout = httpx.Timeout(connect=4.0, read=timeout, write=30.0, pool=4.0)

    # ------------------------------------------------------------------
    # Stato del server
    # ------------------------------------------------------------------
    def props(self, timeout: float = 2.0) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}/props", timeout=timeout)
        response.raise_for_status()
        return response.json()

    def health(self, timeout: float = 1.5) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=timeout)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def probe(self) -> dict[str, Any]:
        try:
            data = self.props()
        except (httpx.HTTPError, ValueError):
            return {"up": False, "sleeping": False, "model": ""}
        model = ""
        meta = data.get("model") or {}
        if isinstance(meta, dict):
            model = meta.get("name") or meta.get("path") or ""
        return {
            "up": True,
            "sleeping": bool(data.get("is_sleeping")),
            "model": model,
        }

    def resolve_model(self) -> str:
        if self._model:
            return self._model
        try:
            response = httpx.get(f"{self.base_url}/v1/models", timeout=3.0)
            response.raise_for_status()
            models = response.json().get("data") or []
            if models:
                self._model = str(models[0].get("id") or "")
        except (httpx.HTTPError, ValueError):
            pass
        if not self._model:
            self._model = "local"
        return self._model

    def wait_until_ready(self, timeout: float = 120.0, interval: float = 0.5) -> bool:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.health():
                return True
            time.sleep(interval)
        return False

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        payload = {
            "model": self.resolve_model(),
            "messages": list(messages),
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=self._timeout,
        )
        self._raise_for_status(response)
        data = response.json()
        return str(data["choices"][0]["message"].get("content") or "")

    def stream_chat(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        on_delta: DeltaCallback,
        on_done: DoneCallback | None = None,
        on_error: ErrorCallback | None = None,
        stop_event: threading.Event | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        payload = {
            "model": self.resolve_model(),
            "messages": list(messages),
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        collected: list[str] = []
        failed = False
        try:
            with httpx.Client(timeout=self._timeout) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = response.read().decode("utf-8", "replace")
                        raise LlamaError(
                            f"llama-server ha risposto {response.status_code}: {body[:400]}"
                        )
                    for line in response.iter_lines():
                        if stop_event is not None and stop_event.is_set():
                            break
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        content = delta.get("content")
                        if content:
                            collected.append(content)
                            on_delta(content)
        except Exception as exc:  # noqa: BLE001 - il chiamante decide come mostrarlo
            failed = True
            if on_error is not None:
                on_error(exc)
            else:
                raise
        text = "".join(collected)
        if not failed and on_done is not None:
            on_done(text)
        return text

    # ------------------------------------------------------------------
    # Text to speech
    # ------------------------------------------------------------------
    @contextmanager
    def tts_stream_pcm(
        self,
        text: str,
        *,
        lang: str = "it",
        voice: str = "female",
        speaker_ref: str = "",
        timeout: float = 300.0,
    ) -> Iterator[tuple[int, Iterator[bytes]]]:
        """Apre /tts in streaming e restituisce (sample_rate, chunk_iterator)."""
        payload: dict[str, Any] = {
            "input": text,
            "lang": lang,
            "voice": voice,
            "response_format": "pcm",
            "stream": True,
        }
        if speaker_ref:
            from pathlib import Path

            sample = Path(speaker_ref).expanduser()
            if not sample.is_file():
                raise LlamaError(f"Campione vocale non trovato: {sample}")
            import base64

            payload["speaker_ref_b64"] = base64.b64encode(sample.read_bytes()).decode("ascii")

        with httpx.Client(
            timeout=httpx.Timeout(connect=4.0, read=timeout, write=60.0, pool=4.0)
        ) as client:
            with client.stream("POST", f"{self.base_url}/tts", json=payload) as response:
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", "replace")
                    raise LlamaError(
                        f"llama-server ha risposto {response.status_code}: {body[:400]}"
                    )
                content_type = response.headers.get("content-type", "")
                if not content_type.startswith("audio/"):
                    body = response.read().decode("utf-8", "replace")
                    raise LlamaError(
                        f"Risposta TTS inattesa ({content_type or 'senza tipo'}): {body[:300]}"
                    )
                match = re.search(r"rate=(\d+)", content_type)
                rate = int(match.group(1)) if match else 24000
                yield rate, response.iter_bytes()

    def tts(
        self,
        text: str,
        *,
        lang: str = "it",
        voice: str = "female",
        speaker_ref: str = "",
        timeout: float = 300.0,
    ) -> bytes:
        payload: dict[str, Any] = {
            "input": text,
            "lang": lang,
            "voice": voice,
            "response_format": "wav",
        }
        if speaker_ref:
            from pathlib import Path

            sample = Path(speaker_ref).expanduser()
            if not sample.is_file():
                raise LlamaError(f"Campione vocale non trovato: {sample}")
            import base64

            payload["speaker_ref_b64"] = base64.b64encode(sample.read_bytes()).decode("ascii")

        response = httpx.post(
            f"{self.base_url}/tts",
            json=payload,
            timeout=httpx.Timeout(connect=4.0, read=timeout, write=60.0, pool=4.0),
        )
        self._raise_for_status(response)
        content = response.content
        if not content:
            raise LlamaError("Il server TTS ha restituito un audio vuoto.")
        if content.lstrip()[:1] == b"{":
            raise LlamaError(f"Risposta TTS inattesa: {content[:300]!r}")
        return content

    # ------------------------------------------------------------------
    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:400]
        raise LlamaError(f"llama-server ha risposto {response.status_code}: {detail}")
