"""Applicazione GTK4/Adw: launcher, chat in streaming e TTS degli appunti."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .config import Config  # noqa: E402
from .llm import LlamaClient  # noqa: E402
from .paths import APP_ID  # noqa: E402
from .server import ServerError, ServerManager  # noqa: E402
from .textutil import preview  # noqa: E402
from .tts import TTSService  # noqa: E402

CSS = """
window.assistant-window {
    background: transparent;
}
.launcher {
    background: @window_bg_color;
    border: 1px solid alpha(@borders, 0.85);
    border-radius: 18px;
    box-shadow: 0 24px 64px rgba(0, 0, 0, 0.38);
}
.header {
    padding: 10px 14px;
}
.title {
    font-weight: 700;
}
.dot {
    font-size: 12px;
}
.status-ok { color: #2ec27e; }
.status-warn { color: #e5a50a; }
.status-error { color: #e01b24; }
.transcript, .transcript text {
    background: transparent;
}
.transcript text {
    font-size: 14px;
}
.entry {
    font-size: 16px;
    padding: 8px 12px;
}
.chip {
    border-radius: 999px;
    padding: 4px 10px;
}
.hint {
    opacity: 0.65;
    font-size: 12px;
}
.error-text {
    color: #e01b24;
}
.bottom {
    padding: 10px 14px 14px 14px;
}
"""


def _ui(function: Callable[..., Any], *args: Any) -> None:
    """Esegue una callback sul thread GTK."""

    def invoke() -> bool:
        try:
            function(*args)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        return False

    GLib.idle_add(invoke)


class AssistantApplication(Adw.Application):
    def __init__(self, config: Config) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.config = config
        self.client = LlamaClient(config.server.base_url, config.llm.model)
        self.server = ServerManager(config.server, self.client)
        self.tts = TTSService(self.client, config.tts)
        self.window: AssistantWindow | None = None

    # ------------------------------------------------------------------
    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        # Mantiene viva l'istanza primaria anche quando il launcher e' nascosto.
        # Va qui e non in __init__: le istanze secondarie devono uscire subito
        # dopo aver inoltrato il comando.
        self.hold()
        display = Gdk.Display.get_default()
        if display is not None:
            provider = Gtk.CssProvider()
            try:
                provider.load_from_data(CSS.encode("utf-8"))
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
            except GLib.Error:
                traceback.print_exc()
        self._install_actions()

    def do_activate(self) -> None:
        self.show_launcher()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        arguments = command_line.get_arguments()[1:]
        action = arguments[0] if arguments else "toggle"

        if action == "run":
            self.show_launcher()
        elif action == "toggle":
            self.toggle_launcher()
        elif action in {"speak", "speak-clipboard"}:
            self.show_launcher(mode="speak")
        elif action == "ask":
            text = " ".join(arguments[1:]).strip()
            self.show_launcher(mode="ask", text=text)
        else:
            command_line.print_literal(f"Azione sconosciuta: {action}\n")
            command_line.set_exit_status(2)
            return 2

        command_line.set_exit_status(0)
        return 0

    def do_shutdown(self) -> None:
        self.tts.stop()
        Adw.Application.do_shutdown(self)

    # ------------------------------------------------------------------
    def show_launcher(self, mode: str | None = None, text: str = "") -> None:
        if self.window is None:
            self.window = AssistantWindow(self)
        self.window.show_mode(mode=mode, text=text)

    def toggle_launcher(self) -> None:
        if self.window is None:
            self.window = AssistantWindow(self)
        window = self.window
        if window.get_visible() and window.is_active():
            window.hide_launcher()
        else:
            window.show_mode()

    def open_config(self) -> None:
        path = self.config.ensure_file()
        try:
            Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
        except GLib.Error:
            traceback.print_exc()

    def quit_app(self) -> None:
        self.tts.stop()
        if self.window is not None:
            self.window.hide_launcher()
        self.release()
        self.quit()

    def _install_actions(self) -> None:
        actions = {
            "new-chat": lambda *_: self.window.new_chat() if self.window else None,
            "open-config": lambda *_: self.open_config(),
            "quit": lambda *_: self.quit_app(),
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)


class AssistantWindow(Adw.ApplicationWindow):
    def __init__(self, app: AssistantApplication) -> None:
        super().__init__(application=app)
        self.app = app
        self.config = app.config
        self.history: list[dict[str, str]] = []
        self.last_answer = ""
        self.clipboard_text = ""
        self._busy = False
        self._answer_started = False
        self._probing = False
        self._mode: str | None = None
        self._stop_stream = threading.Event()
        self._chat_generation = 0
        self._after_present_id = 0
        self._pending_text = ""

        self.set_title("Assistente")
        self.set_default_size(760, 500)
        self.set_resizable(False)
        self.set_decorated(False)
        self.add_css_class("assistant-window")
        self.set_hide_on_close(True)

        self._build_ui()
        self._install_shortcuts()

        self.connect("notify::is-active", self._on_active_changed)
        self.connect("close-request", self._on_close_request)

        self._show_hint()
        threading.Thread(target=self._probe_worker, daemon=True).start()
        GLib.timeout_add_seconds(10, self._probe_tick)

    # ------------------------------------------------------------------
    # Costruzione UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("launcher")
        self.set_content(root)

        header_handle = Gtk.WindowHandle()
        root.append(header_handle)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("header")
        header_handle.set_child(header)

        self.status_dot = Gtk.Label(label="●")
        self.status_dot.add_css_class("dot")
        self.status_dot.add_css_class("status-warn")
        header.append(self.status_dot)

        title = Gtk.Label(label="Assistente")
        title.add_css_class("title")
        header.append(title)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        self.stop_tts_button = Gtk.Button(label="Ferma lettura")
        self.stop_tts_button.add_css_class("flat")
        self.stop_tts_button.set_visible(False)
        self.stop_tts_button.connect("clicked", lambda *_: self.stop_tts())
        header.append(self.stop_tts_button)

        config_button = Gtk.Button.new_from_icon_name("emblem-system-symbolic")
        config_button.add_css_class("flat")
        config_button.set_tooltip_text("Apri configurazione")
        config_button.connect("clicked", lambda *_: self.app.open_config())
        header.append(config_button)

        close_button = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close_button.add_css_class("flat")
        close_button.set_tooltip_text("Nascondi (Esc)")
        close_button.connect("clicked", lambda *_: self.hide_launcher())
        header.append(close_button)

        root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self.transcript = Gtk.TextView()
        self.transcript.set_editable(False)
        self.transcript.set_cursor_visible(False)
        self.transcript.set_focusable(False)
        self.transcript.set_can_focus(False)
        self.transcript.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.transcript.set_left_margin(18)
        self.transcript.set_right_margin(18)
        self.transcript.set_top_margin(14)
        self.transcript.set_bottom_margin(14)
        self.buffer = self.transcript.get_buffer()
        self.buffer.create_tag("user", weight=Pango.Weight.BOLD, foreground="#3584e4")
        self.buffer.create_tag("assistant")
        self.buffer.create_tag("hint", style=Pango.Style.ITALIC, foreground="#888888")
        self.buffer.create_tag("error", foreground="#e01b24")

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.transcript)
        scroller.set_vexpand(True)
        scroller.add_css_class("transcript")
        root.append(scroller)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bottom.add_css_class("bottom")
        root.append(bottom)

        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bottom.append(chips)

        self.clip_button = Gtk.Button(label="Leggi appunti")
        self.clip_button.set_icon_name("edit-copy-symbolic")
        self.clip_button.add_css_class("pill")
        self.clip_button.set_tooltip_text("Sintetizza il testo copiato (Ctrl+Shift+L)")
        self.clip_button.connect("clicked", lambda *_: self.speak_clipboard())
        chips.append(self.clip_button)

        self.answer_button = Gtk.Button(label="Leggi risposta")
        self.answer_button.set_icon_name("audio-speakers-symbolic")
        self.answer_button.add_css_class("pill")
        self.answer_button.set_sensitive(False)
        self.answer_button.set_tooltip_text("Sintetizza l'ultima risposta (Ctrl+Shift+R)")
        self.answer_button.connect("clicked", lambda *_: self.speak_answer())
        chips.append(self.answer_button)

        self.clip_preview = Gtk.Label(label="Nessun testo negli appunti")
        self.clip_preview.add_css_class("hint")
        self.clip_preview.set_xalign(0.0)
        self.clip_preview.set_ellipsize(Pango.EllipsizeMode.END)
        self.clip_preview.set_max_width_chars(78)
        bottom.append(self.clip_preview)

        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bottom.append(entry_row)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Chiedi qualcosa…")
        self.entry.set_hexpand(True)
        self.entry.add_css_class("entry")
        self.entry.connect("activate", lambda *_: self.send())
        entry_row.append(self.entry)

        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        entry_row.append(self.spinner)

        self.send_button = Gtk.Button.new_from_icon_name("go-next-symbolic")
        self.send_button.add_css_class("suggested-action")
        self.send_button.set_tooltip_text("Invia (Invio)")
        self.send_button.connect("clicked", lambda *_: self.send())
        entry_row.append(self.send_button)

        self.status_label = Gtk.Label(label="Verifico llama-server…")
        self.status_label.add_css_class("hint")
        self.status_label.set_xalign(0.0)
        bottom.append(self.status_label)

    def _install_shortcuts(self) -> None:
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.GLOBAL)

        def add(accelerator: str, callback: Callable[[], Any]) -> None:
            try:
                trigger = Gtk.ShortcutTrigger.parse_string(accelerator)
            except (TypeError, ValueError):
                trigger = None
            if trigger is None:
                return

            def invoke(*_args: Any) -> bool:
                callback()
                return True

            action = Gtk.CallbackAction.new(invoke)
            controller.add_shortcut(Gtk.Shortcut.new(trigger, action))

        add("Escape", self._on_escape)
        add("<Control><Shift>l", self.speak_clipboard)
        add("<Control><Shift>r", self.speak_answer)
        add("<Control>n", self.new_chat)
        add("<Control>q", self.app.quit_app)
        self.add_controller(controller)

    # ------------------------------------------------------------------
    # Visibilita'
    # ------------------------------------------------------------------
    def show_mode(self, mode: str | None = None, text: str = "") -> None:
        self._mode = mode
        self._pending_text = text
        if self._after_present_id:
            GLib.source_remove(self._after_present_id)
        self.present()
        self.entry.grab_focus()
        self._after_present_id = GLib.timeout_add(150, self._after_present)

    def _after_present(self) -> bool:
        self._after_present_id = 0
        mode = self._mode
        text = self._pending_text
        self._mode = None
        self._pending_text = ""
        if mode == "ask" and text:
            self.entry.set_text(text)
            self.send()
        elif mode == "speak":
            self._speak_clipboard_with_retry()
        else:
            self._read_clipboard()
        return False

    def hide_launcher(self) -> None:
        # Toglie il focus prima di nascondere: evita il warning di GtkText
        # quando la finestra viene smontata mentre un campo di testo ha il focus.
        if self.get_visible():
            try:
                self.set_focus(None)
            except TypeError:
                pass
        self.set_visible(False)

    def _on_escape(self) -> None:
        if self.app.tts.is_playing:
            self.stop_tts()
        else:
            self.hide_launcher()

    def _on_close_request(self, *_args: Any) -> bool:
        self.hide_launcher()
        return True

    def _on_active_changed(self, *_args: Any) -> None:
        if self.is_active():
            GLib.timeout_add(120, self._refresh_clipboard)
        elif self.config.ui.hide_on_focus_loss:
            GLib.timeout_add(180, self._hide_if_inactive)

    def _hide_if_inactive(self) -> bool:
        if not self.is_active() and not self._busy:
            self.hide_launcher()
        return False

    # ------------------------------------------------------------------
    # Appunti
    # ------------------------------------------------------------------
    def _refresh_clipboard(self) -> bool:
        if self.get_visible() and self.is_active():
            self._read_clipboard()
        return False

    def _read_clipboard(self, callback: Callable[[str], Any] | None = None) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        clipboard = display.get_clipboard()

        def finished(source: Any, result: Any) -> None:
            try:
                text = source.read_text_finish(result) or ""
            except GLib.Error:
                text = ""
            self.clipboard_text = text.strip()
            self._update_clipboard_ui()
            if callback is not None:
                callback(self.clipboard_text)

        clipboard.read_text_async(None, finished)

    def _update_clipboard_ui(self) -> None:
        if self.clipboard_text:
            self.clip_button.set_sensitive(True)
            self.clip_preview.set_text(f"Appunti: “{preview(self.clipboard_text)}”")
            self.clip_preview.set_tooltip_text(self.clipboard_text[:1000])
        else:
            self.clip_button.set_sensitive(False)
            self.clip_preview.set_text("Nessun testo negli appunti")
            self.clip_preview.set_tooltip_text(None)

    def speak_clipboard(self) -> None:
        self._speak_clipboard_with_retry()

    def _speak_clipboard_with_retry(self, attempt: int = 0) -> None:
        """Legge gli appunti, riprovando se il focus Wayland non e' ancora pronto."""

        def handle(text: str) -> None:
            if text:
                self._speak_text(text)
                return
            if attempt >= 8:
                self._set_status("Nessun testo negli appunti", "warn")
                return

            def retry() -> bool:
                self._speak_clipboard_with_retry(attempt + 1)
                return False

            GLib.timeout_add(200, retry)

        self._read_clipboard(handle)

    def speak_answer(self) -> None:
        if not self.last_answer:
            self._set_status("Nessuna risposta da leggere", "warn")
            return
        self._speak_text(self.last_answer)

    def _speak_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self._set_status("Nessun testo negli appunti", "warn")
            return
        if not self.config.tts.enabled:
            self._set_status("TTS disabilitato nella configurazione", "warn")
            return
        self._set_status("Preparo l'audio…", "warn")
        self.app.tts.speak(text, on_state=lambda *args: _ui(self._on_tts_state, *args))
        self.stop_tts_button.set_visible(True)

    def stop_tts(self) -> None:
        self.app.tts.stop()
        self.stop_tts_button.set_visible(False)

    def _on_tts_state(self, state: str, *args: Any) -> None:
        if state == "synthesizing":
            self._set_status("Preparo l'audio…", "warn")
        elif state == "playing":
            self._set_status("Lettura in corso…", "ok")
        elif state == "done":
            self._set_status("Lettura completata", "ok")
            self.stop_tts_button.set_visible(False)
        elif state == "stopped":
            self._set_status("Lettura interrotta", "warn")
            self.stop_tts_button.set_visible(False)
        elif state == "empty":
            self._set_status("Nessun testo da leggere", "warn")
            self.stop_tts_button.set_visible(False)
        elif state == "error":
            message = str(args[0]) if args else "Errore TTS"
            self._set_status(f"TTS: {message}", "error")
            self.stop_tts_button.set_visible(False)

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def send(self) -> None:
        if self._busy:
            return
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")
        self._clear_transcript_if_hint()
        self._append(f"Tu: {text}\n", "user")
        self.history.append({"role": "user", "content": text})
        self._trim_history()
        self._busy = True
        self._answer_started = False
        self._stop_stream.clear()
        self._set_busy(True)
        self._chat_generation += 1
        generation = self._chat_generation
        messages = [
            {"role": "system", "content": self.config.llm.system_prompt},
            *self.history,
        ]
        threading.Thread(
            target=self._stream_worker,
            args=(messages, generation),
            daemon=True,
            name="chat-stream",
        ).start()

    def _stream_worker(self, messages: list[dict[str, str]], generation: int) -> None:
        try:
            self.app.server.ensure_ready(
                on_status=lambda state: _ui(self._apply_server_state, state)
            )
            self.app.client.stream_chat(
                messages,
                on_delta=lambda chunk: _ui(self._on_delta, chunk, generation),
                on_done=lambda text: _ui(self._on_done, text, generation),
                on_error=lambda error: _ui(self._on_error, str(error), generation),
                stop_event=self._stop_stream,
                max_tokens=self.config.llm.max_tokens,
                temperature=self.config.llm.temperature,
            )
        except ServerError as exc:
            _ui(self._on_error, str(exc), generation)
        except Exception as exc:  # noqa: BLE001
            _ui(self._on_error, str(exc), generation)

    def _on_delta(self, chunk: str, generation: int) -> None:
        if generation != self._chat_generation:
            return
        if not self._answer_started:
            self._answer_started = True
            self._append("\nAssistente: ", "assistant")
        self._append(chunk, "assistant")

    def _on_done(self, text: str, generation: int) -> None:
        if generation != self._chat_generation:
            return
        if not self._answer_started:
            self._append("\nAssistente: ", "assistant")
        self._append("\n\n", "assistant")
        answer = text.strip()
        if not answer:
            self._append("(nessuna risposta)\n\n", "hint")
        else:
            self.last_answer = answer
            self.history.append({"role": "assistant", "content": answer})
            self._trim_history()
            self.answer_button.set_sensitive(True)
        self._busy = False
        self._set_busy(False)
        self._set_status("Pronto", "ok")

    def _on_error(self, message: str, generation: int) -> None:
        if generation != self._chat_generation:
            return
        self._append(f"\n[Errore: {message}]\n\n", "error")
        self._busy = False
        self._set_busy(False)
        self._set_status("Errore: llama-server non disponibile", "error")

    def new_chat(self) -> None:
        self._chat_generation += 1
        if self._busy:
            self._stop_stream.set()
            self._busy = False
            self._set_busy(False)
        self.history.clear()
        self.last_answer = ""
        self.answer_button.set_sensitive(False)
        self.buffer.set_text("")
        self._show_hint()
        self.entry.set_text("")
        self.entry.grab_focus()
        self._set_status("Nuova chat", "ok")

    def _trim_history(self) -> None:
        limit = max(2, self.config.llm.max_history_messages)
        if len(self.history) > limit:
            self.history = self.history[-limit:]

    def _set_busy(self, busy: bool) -> None:
        self.spinner.set_visible(busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.send_button.set_sensitive(not busy)
        self.entry.set_sensitive(not busy)
        if not busy and self.get_visible():
            self.entry.grab_focus()

    # ------------------------------------------------------------------
    # Stato server
    # ------------------------------------------------------------------
    def _probe_tick(self) -> bool:
        if not self._probing:
            self._probing = True
            threading.Thread(target=self._probe_worker, daemon=True).start()
        return True

    def _probe_worker(self) -> None:
        state = self.app.server.probe()
        _ui(self._apply_probe, state)

    def _apply_probe(self, state: dict[str, Any]) -> None:
        self._probing = False
        if self.app.tts.is_playing or self._busy:
            return
        if not state.get("up"):
            self._set_status("llama-server non attivo (si avvia alla prima richiesta)", "error")
        elif state.get("sleeping"):
            self._set_status("Modello in pausa (si ricarica alla prima richiesta)", "warn")
        else:
            self._set_status("Pronto", "ok")

    def _apply_server_state(self, state: str) -> None:
        if state == "starting":
            self._set_status("Avvio llama-server…", "warn")
        elif state == "sleeping":
            self._set_status("Carico il modello…", "warn")
        elif state == "ready":
            self._set_status("Pronto", "ok")

    def _set_status(self, text: str, level: str = "ok") -> None:
        self.status_label.set_text(text)
        for css in ("status-ok", "status-warn", "status-error"):
            self.status_dot.remove_css_class(css)
        self.status_dot.add_css_class(f"status-{level}")

    # ------------------------------------------------------------------
    # Testo
    # ------------------------------------------------------------------
    def _show_hint(self) -> None:
        self.buffer.set_text("")
        self._append(
            "Scrivi una domanda e premi Invio.\n"
            "Copia un testo e premi Ctrl+Shift+L per fartelo leggere.\n",
            "hint",
        )

    def _clear_transcript_if_hint(self) -> None:
        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        text = self.buffer.get_text(start, end, True)
        if text.startswith("Scrivi una domanda"):
            self.buffer.set_text("")

    def _append(self, text: str, tag: str | None = None) -> None:
        end = self.buffer.get_end_iter()
        if tag:
            self.buffer.insert_with_tags_by_name(end, text, tag)
        else:
            self.buffer.insert(end, text)
        GLib.idle_add(self._scroll_end)

    def _scroll_end(self) -> bool:
        end = self.buffer.get_end_iter()
        self.transcript.scroll_to_iter(end, 0.0, False, 0.0, 0.0)
        return False
