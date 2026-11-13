"""Sintesi vocale in streaming PCM, cache e riproduzione tramite PipeWire."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import TTSConfig
from .llm import LlamaClient
from .paths import tts_cache_dir
from .textutil import cache_key, split_for_tts

StateCallback = Callable[..., None]


class TTSService:
    def __init__(self, client: LlamaClient, config: TTSConfig) -> None:
        self.client = client
        self.config = config
        self.cache_dir = tts_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._generation = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._state_cb: StateCallback | None = None
        self._playing = False

    # ------------------------------------------------------------------
    # API pubblica
    # ------------------------------------------------------------------
    def speak(self, text: str, on_state: StateCallback | None = None) -> None:
        self.stop()
        if not self.config.enabled:
            if on_state:
                on_state("error", "TTS disabilitato nella configurazione.")
            return

        chunks = split_for_tts(
            text,
            max_chars=self.config.chunk_chars,
            max_total=self.config.max_chars,
        )
        if not chunks:
            if on_state:
                on_state("empty")
            return

        with self._lock:
            self._generation += 1
            generation = self._generation
            self._state_cb = on_state
            self._playing = False

        threading.Thread(
            target=self._worker,
            args=(chunks, generation),
            daemon=True,
            name="tts-worker",
        ).start()

    def stop(self) -> None:
        with self._lock:
            was_active = self._playing or self._proc is not None
            self._generation += 1
            proc = self._proc
            self._proc = None
            callback = self._state_cb
            self._state_cb = None
            self._playing = False

        self._terminate(proc)
        if was_active and callback is not None:
            callback("stopped")

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing or self._proc is not None

    def wait(self, timeout: float = 30.0) -> None:
        import time

        deadline = time.monotonic() + timeout
        while self.is_playing and time.monotonic() < deadline:
            time.sleep(0.1)

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    def _worker(self, chunks: list[str], generation: int) -> None:
        try:
            total = len(chunks)
            for index, chunk in enumerate(chunks):
                if not self._current(generation):
                    return
                self._emit(generation, "synthesizing", index, total)
                self._play_chunk(chunk, generation, index, total)
                if not self._current(generation):
                    return
            self._emit(generation, "done")
        except Exception as exc:  # noqa: BLE001
            if self._current(generation):
                self._emit(generation, "error", str(exc))

    def _play_chunk(self, text: str, generation: int, index: int, total: int) -> None:
        if not shutil.which("pw-play"):
            self._play_chunk_buffered(text, generation, index, total)
            return

        key = cache_key(
            "pcm-v1",
            self.config.lang,
            self.config.voice,
            self.config.speaker_ref,
            text,
        )
        pcm_path = self.cache_dir / f"{key}.pcm"
        meta_path = self.cache_dir / f"{key}.json"

        if self.config.cache and pcm_path.is_file() and meta_path.is_file():
            try:
                rate = int(json.loads(meta_path.read_text(encoding="utf-8"))["rate"])
            except (OSError, ValueError, KeyError, TypeError):
                rate = 24000
            self._emit(generation, "playing", index, total)
            self._play_pcm_file(pcm_path, rate, generation)
            return

        with self.client.tts_stream_pcm(
            text,
            lang=self.config.lang,
            voice=self.config.voice,
            speaker_ref=self.config.speaker_ref,
        ) as (rate, stream):
            self._emit(generation, "playing", index, total)
            proc = self._start_stream_player(rate)
            temporary = pcm_path.with_suffix(".tmp") if self.config.cache else None
            handle = open(temporary, "wb") if temporary is not None else None
            try:
                for data in stream:
                    if not self._current(generation):
                        break
                    if not data:
                        continue
                    if handle is not None:
                        handle.write(data)
                    try:
                        assert proc.stdin is not None
                        proc.stdin.write(data)
                    except (BrokenPipeError, OSError):
                        break
            finally:
                if handle is not None:
                    handle.close()
                self._close_stdin(proc)
                self._wait_player(proc, generation)

            if not self._current(generation):
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                return
            if temporary is not None and self.config.cache:
                os.replace(temporary, pcm_path)
                meta_path.write_text(
                    json.dumps({"rate": rate}), encoding="utf-8"
                )
                self._cleanup_cache()

    def _play_chunk_buffered(
        self, text: str, generation: int, index: int, total: int
    ) -> None:
        key = cache_key(
            "wav-v1",
            self.config.lang,
            self.config.voice,
            self.config.speaker_ref,
            text,
        )
        path = self.cache_dir / f"{key}.wav"
        if not (self.config.cache and path.is_file() and path.stat().st_size > 44):
            audio = self.client.tts(
                text,
                lang=self.config.lang,
                voice=self.config.voice,
                speaker_ref=self.config.speaker_ref,
            )
            if not self._current(generation):
                return
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(audio)
            os.replace(temporary, path)
            self._cleanup_cache()

        self._emit(generation, "playing", index, total)
        command = self._player_command(path)
        if command is None:
            raise RuntimeError(
                "Nessun riproduttore audio trovato. Installa pipewire-bin o ffmpeg."
            )
        proc = subprocess.Popen(  # noqa: S603 - argv fisso, nessuna shell
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with self._lock:
            self._proc = proc
            self._playing = True
        try:
            proc.wait()
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
                self._playing = False

    # ------------------------------------------------------------------
    # Riproduzione
    # ------------------------------------------------------------------
    def _start_stream_player(self, rate: int) -> subprocess.Popen[bytes]:
        executable = shutil.which("pw-play")
        if not executable:
            raise RuntimeError("pw-play non trovato.")
        proc = subprocess.Popen(  # noqa: S603 - argv fisso, nessuna shell
            [
                executable,
                "--raw",
                "--format",
                "f32",
                "--rate",
                str(rate),
                "--channels",
                "1",
                "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with self._lock:
            self._proc = proc
            self._playing = True
        return proc

    def _play_pcm_file(self, path: Path, rate: int, generation: int) -> None:
        proc = self._start_stream_player(rate)
        try:
            with path.open("rb") as handle:
                while self._current(generation):
                    data = handle.read(65536)
                    if not data:
                        break
                    try:
                        assert proc.stdin is not None
                        proc.stdin.write(data)
                    except (BrokenPipeError, OSError):
                        break
        finally:
            self._close_stdin(proc)
            self._wait_player(proc, generation)

    def _wait_player(self, proc: subprocess.Popen[bytes], generation: int) -> None:
        try:
            returncode = proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
            returncode = proc.wait()
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
                self._playing = False
        if returncode != 0 and self._current(generation):
            raise RuntimeError(f"Riproduzione audio fallita (codice {returncode}).")

    @staticmethod
    def _close_stdin(proc: subprocess.Popen[bytes]) -> None:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass

    def _player_command(self, path: Path) -> list[str] | None:
        configured = self.config.player
        if configured and shutil.which(configured):
            return [configured, str(path)]
        for player, extra in (
            ("pw-play", []),
            ("paplay", []),
            ("aplay", ["-q"]),
            ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
        ):
            executable = shutil.which(player)
            if executable:
                return [executable, *extra, str(path)]
        return None

    def _cleanup_cache(self) -> None:
        files = [
            item
            for pattern in ("*.pcm", "*.wav")
            for item in self.cache_dir.glob(pattern)
            if item.is_file()
        ]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        total = 0
        for index, item in enumerate(files):
            size = item.stat().st_size
            total += size
            if index >= 200 or total > 200 * 1024 * 1024:
                try:
                    item.unlink()
                    item.with_suffix(".json").unlink(missing_ok=True)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    def _current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def _emit(self, generation: int, state: str, *args: Any) -> None:
        with self._lock:
            if generation != self._generation:
                return
            callback = self._state_cb
        if callback is not None:
            callback(state, *args)

    @staticmethod
    def _terminate(proc: subprocess.Popen[bytes] | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
