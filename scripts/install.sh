#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/usr/bin/python3}"
VENV="${VENV:-$HOME/.local/share/ai-desktop-assistant/venv}"
LLAMA_DIR="${LLAMA_DIR:-$HOME/.local/opt/llama.cpp}"
LLAMA_VERSION="${LLAMA_VERSION:-b12034}"
LLAMA_BUILD_DIR="$LLAMA_DIR/llama-$LLAMA_VERSION"
LLAMA_SERVER="$LLAMA_BUILD_DIR/llama-server"
# MX250 (Vulkan1) e' molto piu' veloce della iGPU/CPU. Sovrascrivi con
# EXTRA_ARGS="-ngl 0" se non hai una GPU dedicata.
EXTRA_ARGS="${EXTRA_ARGS:--ngl 99 --device Vulkan1}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_NAME="ai-assistant-llama.service"
NO_START="${NO_START:-0}"

say() { printf '\n==> %s\n' "$*"; }
warn() { printf 'ATTENZIONE: %s\n' "$*" >&2; }

say "Controllo i prerequisiti"
if [[ ! -x "$PYTHON" ]]; then
  echo "Python non trovato: $PYTHON" >&2
  exit 1
fi

if ! "$PYTHON" - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: F401
print("GTK4/libadwaita OK")
PY
then
  cat >&2 <<'EOF'
Mancano GTK4/libadwaita per Python. Su Ubuntu:
  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-venv
EOF
  exit 1
fi

for command in curl tar systemctl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Comando mancante: $command" >&2
    exit 1
  fi
done

if ! command -v pw-play >/dev/null 2>&1; then
  warn "pw-play non trovato: installa pipewire-bin per il TTS."
fi

say "Creo l'ambiente Python (con accesso a PyGObject di sistema)"
mkdir -p "$(dirname "$VENV")" "$HOME/.local/bin"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -e "$ROOT"

say "Installo il comando ai-assistant"
ln -sfn "$VENV/bin/ai-assistant" "$HOME/.local/bin/ai-assistant"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) warn "Aggiungi $HOME/.local/bin al PATH." ;;
esac

say "Preparo llama.cpp $LLAMA_VERSION"
mkdir -p "$LLAMA_DIR"
if [[ ! -x "$LLAMA_SERVER" ]]; then
  archive="/tmp/llama-$LLAMA_VERSION-vulkan.tar.gz"
  url="https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_VERSION/llama-$LLAMA_VERSION-bin-ubuntu-vulkan-x64.tar.gz"
  curl -L --fail --retry 3 -o "$archive" "$url"
  tar -xzf "$archive" -C "$LLAMA_DIR"
  rm -f "$archive"
fi
if [[ ! -x "$LLAMA_SERVER" ]]; then
  echo "llama-server non trovato dopo l'installazione: $LLAMA_SERVER" >&2
  exit 1
fi
"$LLAMA_SERVER" --version | head -1

say "Installo il servizio utente"
mkdir -p "$UNIT_DIR"
sed \
  -e "s|@LLAMA_SERVER@|$LLAMA_SERVER|g" \
  -e "s|@EXTRA_ARGS@|$EXTRA_ARGS|g" \
  "$ROOT/data/ai-assistant-llama.service.in" > "$UNIT_DIR/$UNIT_NAME"
systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME" >/dev/null

say "Installo la voce nel menu applicazioni"
mkdir -p "$HOME/.local/share/applications"
sed "s|^Exec=.*|Exec=$HOME/.local/bin/ai-assistant run|" \
  "$ROOT/data/ai-assistant.desktop" > "$HOME/.local/share/applications/ai-assistant.desktop"
chmod 0644 "$HOME/.local/share/applications/ai-assistant.desktop"

say "Installo le scorciatoie GNOME"
if "$VENV/bin/ai-assistant" install-shortcuts; then
  :
else
  warn "Non riesco a installare le scorciatoie automaticamente. Vedi README."
fi

if [[ "$NO_START" != "1" ]]; then
  say "Avvio llama-server (il primo avvio scarica il modello, ~1.6 GB)"
  if curl -fsS --max-time 2 http://127.0.0.1:8090/health >/dev/null 2>&1; then
    warn "Un server risponde gia' sulla porta 8090: fermalo prima di usare il servizio."
  else
    systemctl --user restart "$UNIT_NAME"
    systemctl --user --no-pager --lines=5 status "$UNIT_NAME" || true
  fi
fi

cat <<EOF

Installazione completata.

  Launcher:       Ctrl+Alt+Space
  Leggi appunti:  Ctrl+Alt+R
  Diagnostica:    ai-assistant doctor
  Stato server:   ai-assistant server-status
  Log server:     journalctl --user -u $UNIT_NAME -f

Il primo avvio di llama-server scarica LFM3-Omni-2B Q4_K_M e il mmproj.
EOF
