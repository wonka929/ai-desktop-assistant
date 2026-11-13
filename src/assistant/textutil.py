"""Pulizia e suddivisione del testo per il TTS."""

from __future__ import annotations

import re

_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_HEADING = re.compile(r"^\s{0,3}[#>*+\-]+\s*", re.MULTILINE)
_EMPHASIS = re.compile(r"[*_~]{1,3}")
_SPACES = re.compile(r"[ \t]+")
_NEWLINES = re.compile(r"\n{2,}")


def strip_for_speech(text: str) -> str:
    """Rende un testo markdown/HTML-ish adatto alla lettura ad alta voce."""
    if not text:
        return ""
    text = _FENCED_CODE.sub(" ", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _HEADING.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACES.sub(" ", text)
    text = _NEWLINES.sub("\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip()


def _split_long_piece(piece: str, max_chars: int) -> list[str]:
    """Spezza una frase troppo lunga su virgole, punti e virgola o spazi."""
    if len(piece) <= max_chars:
        return [piece]

    pieces: list[str] = []
    remaining = piece
    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        cut = max(
            window.rfind(", "),
            window.rfind("; "),
            window.rfind(": "),
            window.rfind(" "),
        )
        if cut < max_chars // 3:
            cut = max_chars
        else:
            cut += 1
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return [p for p in pieces if p]


def split_for_tts(text: str, max_chars: int = 320, max_total: int = 4000) -> list[str]:
    """Divide il testo in chunk pronunciabili, rispettando le frasi."""
    clean = strip_for_speech(text)
    if not clean:
        return []
    if len(clean) > max_total:
        clean = clean[:max_total].rsplit(" ", 1)[0].rstrip(",;:") + "..."

    sentences = re.split(r"(?<=[.!?…])\s+|\n+", clean)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        for piece in _split_long_piece(sentence, max_chars):
            if not current:
                current = piece
            elif len(current) + 1 + len(piece) <= max_chars:
                current = f"{current} {piece}"
            else:
                chunks.append(current)
                current = piece

    if current:
        chunks.append(current)
    return chunks


def preview(text: str, limit: int = 110) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def cache_key(*parts: str) -> str:
    import hashlib

    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]
