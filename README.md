# AI Desktop Assistant

Launcher locale per GNOME basato su **llama.cpp** e **LFM3-Omni-2B**. Nessun
servizio cloud, nessuna API key: il modello gira sul tuo computer e ascolta solo
su `127.0.0.1`.

## Cosa fa

- Finestra “spotlight” richiamata da `Ctrl+Alt+Space`.
- Chat in italiano con risposte in streaming.
- **TTS dagli appunti**: copi un testo, premi `Ctrl+Alt+R` e te lo senti leggere.
- Pulsante “Leggi risposta” per ascoltare l’ultima risposta del modello.
- Il server llama.cpp resta caricato e va in pausa dopo 5 minuti di inattività.

Non esegue comandi di sistema, non apre applicazioni e non esplora file: parla
solo con `llama-server` su localhost, legge gli appunti quando la finestra ha il
focus e riproduce audio. L’unico file che può leggere è l’eventuale campione
vocale configurato in `tts.speaker_ref`.

## Requisiti

- Linux con GNOME su Wayland (testato su GNOME 50.1, Ubuntu 26.04).
- GTK 4 + libadwaita + PyGObject (`python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`).
- PipeWire (`pw-play`) o `ffmpeg` per l’audio.
- llama.cpp **b11960+** (LFM3 è supportato da v0.8.0). Lo script installa
  `b12034` in `~/.local/opt/llama.cpp` senza toccare `/usr/local`.
- Circa 1,6 GB di spazio per modello e mmproj.
- Consigliata una GPU Vulkan con almeno 2 GB di VRAM. Su questa macchina la
  MX250 viene usata con `-ngl 99 --device Vulkan1`; senza GPU dedicata imposta
  `EXTRA_ARGS="-ngl 0"` durante l’installazione.

## Installazione

```bash
cd ~/GITHUB/ai-desktop-assistant
./scripts/install.sh
```

Lo script:

1. crea un venv in `~/.local/share/ai-desktop-assistant/venv` con accesso a
   PyGObject di sistema;
2. installa il comando `ai-assistant` in `~/.local/bin`;
3. scarica llama.cpp `b12034` (build Vulkan) in `~/.local/opt/llama.cpp`;
4. installa il servizio utente `ai-assistant-llama.service`;
5. registra le scorciatoie GNOME;
6. avvia il server: **il modello viene scaricato da `llama-server -hf`** al primo
   avvio (Q4_K_M + mmproj, circa 1,6 GB), con `-ngl 99 --device Vulkan1` e
   `--no-warmup`.

Per controllare il download:

```bash
journalctl --user -u ai-assistant-llama.service -f
```

Se llama-server è già in esecuzione sulla porta 8090, fermalo prima:

```bash
systemctl --user stop ai-assistant-llama.service
# oppure interrompi il processo manuale
```

## Uso

| Scorciatoia | Azione |
|---|---|
| `Ctrl+Alt+Space` | apre/nasconde il launcher |
| `Ctrl+Alt+R` | apre il launcher e legge il testo negli appunti |
| `Invio` | invia la domanda |
| `Ctrl+Shift+L` | legge gli appunti |
| `Ctrl+Shift+R` | legge l’ultima risposta |
| `Ctrl+N` | nuova chat |
| `Esc` | ferma la lettura o nasconde |
| `Ctrl+Q` | esce dall’app |

Flusso TTS tipico:

1. Selezioni un testo nel browser e fai `Ctrl+C`.
2. Premi `Ctrl+Alt+R`.
3. Il launcher appare, legge gli appunti e riproduce l’audio in italiano.

La lettura è a chunk: l’audio parte appena il primo pezzo è pronto, mentre gli
altri vengono sintetizzati. Il TTS usa PCM in streaming verso `pw-play` e mette
in cache i pezzi in `~/.cache/ai-desktop-assistant/tts/`.

## Configurazione

File: `~/.config/ai-desktop-assistant/config.toml`

```toml
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
model = ""              # vuoto = primo modello esposto dal server
max_tokens = 1024
temperature = 0.0
max_history_messages = 16
system_prompt = "..."

[tts]
enabled = true
lang = "it"
voice = "female"        # female | male
speaker_ref = ""        # wav opzionale con un campione di voce
chunk_chars = 320
max_chars = 4000
cache = true
player = "pw-play"
```

Dopo una modifica riavvia l’app (`Ctrl+Q` e riapri) oppure usa “Nuova chat” per
la cronologia.

### Voce di riferimento

Il model card di LFM3 consiglia di passare un campione vocale a `/tts`. Se la
voce predefinita non ti convince, imposta `tts.speaker_ref` con il percorso di
un wav (idealmente la tua voce, 10–20 secondi, mono). Resta tutto in locale.

## Comandi utili

```bash
ai-assistant doctor            # diagnostica
ai-assistant setup             # crea la config
ai-assistant toggle            # apre il launcher
ai-assistant speak             # legge gli appunti
ai-assistant ask "Ciao"        # invia una domanda al launcher
ai-assistant server-start      # avvia llama-server
ai-assistant server-status
ai-assistant install-shortcuts
ai-assistant uninstall-shortcuts
```

## Architettura

```
Ctrl+Alt+Space / Ctrl+Alt+R
        │
        ▼
ai-assistant (GTK4/Adw, single instance)
   ├── Gdk.Clipboard
   ├── POST /v1/chat/completions (streaming)
   ├── POST /tts (PCM streaming) → pw-play
   └── config TOML + cache
              ▲
   llama-server (systemd --user)
   LFM3-Omni-2B Q4_K_M + mmproj Q4_0
   -ngl 99 --device Vulkan1 --no-warmup
   --sleep-idle-seconds 300
```

`--no-warmup` è necessario sulla MX250 da 2 GB: senza, il warmup del vision
encoder supera la VRAM disponibile e llama-server va in OOM. Testo e TTS
funzionano comunque; le immagini non fanno parte dell’MVP.

Perché un server separato: il modello resta caricato tra un uso e l’altro,
l’app resta leggera e il TTS continua anche se il launcher si nasconde. Dopo
5 minuti senza richieste llama-server scarica il modello dalla RAM e lo ricarica
alla richiesta successiva.

## Prestazioni misurate

Su i5-8265U + MX250 2 GB, LFM3-Omni-2B Q4_K_M:

| Carico | MX250 (Vulkan1) | UHD 620 (Vulkan0) | CPU |
|---|---:|---:|---:|
| Testo | ~25–30 tok/s | ~4,5 tok/s | ~12 tok/s |
| TTS (3,6 s di audio) | ~3,4 s | ~41 s | ~26 s |

Con il TTS in streaming il primo blocco PCM arriva dopo circa 1 secondo; la
sintesi dei chunk successivi continua mentre l’audio è già in riproduzione.

## Limiti noti

- **Wayland e appunti**: un’app può leggere il clipboard solo quando ha il
  focus. Per questo `Ctrl+Alt+R` apre e focalizza il launcher; non c’è lettura
  silenziosa in background.
- **Posizionamento**: su Wayland un’app GTK non può posizionarsi in alto al
  centro. La finestra viene centrata dal compositore. Per un vero effetto
  Spotlight serve un’estensione GNOME.
- **VRAM**: con 2 GB la MX250 è al limite. Se cambi modello o contesto, riduci
  `-ngl` o passa a `-ngl 0`; non rimuovere `--no-warmup`.
- **TTS senza GPU**: su CPU la sintesi può essere più lenta del parlato. Se è
  troppo lenta, usa un modello TTS dedicato (es. Piper) solo per gli appunti; la
  chat resta su LFM3-Omni.
- **LFM3 è recente**: la build di llama.cpp è fissata a `b12034`. Non aggiornare
  a caso; se qualcosa si rompe, prova `b11960` (prima versione supportata).
- **Licenza modello**: LFM Open License v1.0. Verifica i termini se distribuisci
  l’app o il modello.

## Sviluppo

```bash
/usr/bin/python3 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
ai-assistant doctor
```

File principali:

- `src/assistant/app.py` — UI GTK4/Adw, chat, appunti, stato.
- `src/assistant/llm.py` — client OpenAI-compatible + `/tts`.
- `src/assistant/tts.py` — chunking, cache, riproduzione.
- `src/assistant/server.py` — avvio/attesa di llama-server.
- `src/assistant/hotkeys.py` — scorciatoie GNOME via gsettings.
- `data/ai-assistant-llama.service.in` — unit systemd.
