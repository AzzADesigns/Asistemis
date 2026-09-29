"""Órdenes para Claude: las pasa a un `claude -p` que queda abierto y las muestra en un panel.

El proceso se arranca de antemano y sigue vivo entre órdenes (ahorra ~4 s por orden);
toda la conversación ocurre en él. Claude trabaja en la carpeta `claude/` (ver su
CLAUDE.md) y solo puede leer, buscar, abrir aplicaciones y anotar: todo lo demás lo
rechaza el modo "dontAsk".
"""

import json
import logging
import queue
import shutil
import subprocess
import threading
from pathlib import Path

import customtkinter as ctk

from herramientas import NO_WINDOW, save_note

CLAUDE_DIR = Path(__file__).resolve().parent / "claude"
ALLOWED_TOOLS = ["Read", "Glob", "Grep", "WebSearch", "WebFetch",
                 "Bash(./abrir.cmd *)", "Bash(./anotar.cmd *)",
                 "PowerShell(./abrir.cmd *)", "PowerShell(./anotar.cmd *)",
                 r"PowerShell(.\abrir.cmd *)", r"PowerShell(.\anotar.cmd *)"]
TIMEOUT = 600  # s
ERROR_LOG = Path(__file__).resolve().parent / "claude-errores.log"

# qué se ve mientras Claude usa cada herramienta
TOOL_LABELS = {"Read": "Leyendo…", "Glob": "Buscando archivos…", "Grep": "Buscando…",
               "WebSearch": "Buscando en la web…", "WebFetch": "Leyendo una página…"}

log = logging.getLogger("asistemis")


class ClaudeSession:
    """Un `claude -p` con entrada stream-json: recibe órdenes por stdin y responde por stdout.
    Avisa con on_event("progress", texto) y on_event("answer", respuesta, es_error)."""

    def __init__(self, on_event):
        self.on_event = on_event
        self.proc = None
        self.session = None  # para retomar la conversación si el proceso se cierra
        self.waiting = False
        self.timer = None

    def start(self):
        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError("No encontré Claude Code (el comando «claude»).")
        cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--permission-mode", "dontAsk", "--effort", "low", "--allowedTools", *ALLOWED_TOOLS]
        if self.session:
            cmd += ["--resume", self.session]
        errors = open(ERROR_LOG, "a", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=CLAUDE_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=errors, text=True, encoding="utf-8", errors="replace",
                                     creationflags=NO_WINDOW)
        errors.close()
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def send(self, order):
        if not self.proc or self.proc.poll() is not None:
            self.start()
        self.waiting = True
        self.timer = threading.Timer(TIMEOUT, self._timeout, args=(self.proc,))
        self.timer.start()
        message = {"type": "user", "message": {"role": "user", "content": order}}
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def reset(self):
        """Nueva conversación: otro proceso, sin retomar la anterior."""
        self.session = None
        self._answered()
        self.stop()
        self.start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None

    def _timeout(self, proc):
        if proc is self.proc and self.waiting:
            log.error("claude tardó más de %s s", TIMEOUT)
            self.stop()  # _read avisa del error al cerrarse

    def _read(self, proc):
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if proc is not self.proc:
                return
            self.session = event.get("session_id", self.session)
            if event.get("type") == "assistant":
                for block in event["message"].get("content", []):
                    if block.get("type") == "tool_use":
                        self.on_event("progress", tool_label(block))
            elif event.get("type") == "result":
                self._answered()
                self.on_event("answer", event.get("result") or "(sin respuesta)", bool(event.get("is_error")))
        # el proceso terminó: si había una orden en curso, avisar
        if proc is self.proc or self.proc is None:
            if self.waiting:
                self._answered()
                self.on_event("answer", "Claude se cerró sin responder (ver claude-errores.log).", True)

    def _answered(self):
        self.waiting = False
        if self.timer:
            self.timer.cancel()


def tool_label(block):
    command = str(block.get("input", {}).get("command", ""))
    if "abrir.cmd" in command:
        return "Abriendo " + command.split("abrir.cmd", 1)[1].strip(" '\"") + "…"
    if "anotar.cmd" in command:
        return "Anotando…"
    return TOOL_LABELS.get(block.get("name"), "Trabajando…")


# --- Panel ------------------------------------------------------------------

BG, CARD, BORDER = "#16171b", "#1f2026", "#2c2e36"
FG, MUTED, ACCENT, GREEN, RED = "#ececef", "#8b8d98", "#4f8cff", "#3ecf8e", "#ff5a5a"
FONT = "Segoe UI"


class Panel:
    """Conversación con Claude: órdenes por voz o escritas y sus respuestas."""

    def __init__(self, root):
        ctk.set_appearance_mode("dark")
        self.root = root
        self.events = queue.Queue()  # del hilo de Claude a la ventana
        self.pending = []            # órdenes en cola mientras Claude trabaja
        self.busy = False
        self.claude = ClaudeSession(lambda *event: self.events.put(event))
        threading.Thread(target=self._warm_up, daemon=True).start()

        win = self.win = ctk.CTkToplevel(root, fg_color=BG)
        win.title("Asistemis · Claude")
        win.geometry(self._geometry())
        win.minsize(380, 420)
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        win.withdraw()

        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 8))
        ctk.CTkLabel(header, text="Claude", text_color=FG, font=(FONT, 20, "bold")).pack(side="left")
        self.status = ctk.CTkLabel(header, text="●  Listo", text_color=GREEN, font=(FONT, 12))
        self.status.pack(side="left", padx=(12, 0), pady=(4, 0))
        ctk.CTkButton(header, text="Nueva conversación", width=10, height=28, corner_radius=14,
                      fg_color=CARD, hover_color=BORDER, text_color=FG, font=(FONT, 12),
                      command=self.new_conversation).pack(side="right")

        self.chat = ctk.CTkTextbox(win, fg_color=CARD, border_color=BORDER, border_width=1,
                                   corner_radius=14, text_color=FG, font=(FONT, 13), wrap="word",
                                   border_spacing=12)
        self.chat.pack(fill="both", expand=True, padx=18)
        self.chat.tag_config("you", foreground=ACCENT, spacing1=10)
        self.chat.tag_config("claude", foreground=FG, spacing1=4, spacing3=6)
        self.chat.tag_config("meta", foreground=MUTED)
        self.chat.tag_config("error", foreground=RED, spacing1=4, spacing3=6)
        self._write("Di «Asistemis, ejecuta…» o escribe una orden abajo.\n", "meta")

        box = ctk.CTkFrame(win, fg_color=CARD, border_color=BORDER, border_width=1, corner_radius=14)
        box.pack(fill="x", padx=18, pady=(10, 6))
        self.input = ctk.CTkTextbox(box, height=64, fg_color="transparent", text_color=FG,
                                    font=(FONT, 13), wrap="word", activate_scrollbars=False)
        self.input.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=6)
        self.input.bind("<Return>", self._on_enter)
        self.send = ctk.CTkButton(box, text="Enviar", width=76, height=36, corner_radius=18,
                                  fg_color=ACCENT, hover_color="#3b74e0", font=(FONT, 13, "bold"),
                                  command=self._send_typed)
        self.send.pack(side="right", padx=10, pady=10, anchor="s")
        ctk.CTkLabel(win, text="Enter para enviar · Shift+Enter nueva línea · se guarda en tus notas",
                     text_color=MUTED, font=(FONT, 11)).pack(pady=(0, 10))

        self._poll()

    def _geometry(self):
        w, h = 460, 620
        x = self.root.winfo_screenwidth() - w - 40
        return f"{w}x{h}+{x}+60"

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.after(300, lambda: self.win.attributes("-topmost", False))
        self.input.focus_set()

    def submit(self, order, source):
        """Recibe una orden (source: "voz" o "escrita"), la anota y se la pasa a Claude."""
        order = order.strip()
        if not order:
            return
        save_note(order, tag=f"[orden {source}]")
        self._write(f"Tú ({source}): ", "you")
        self._write(order + "\n", "you")
        self.show()
        self.pending.append(order)
        self._next()

    def new_conversation(self):
        self.pending.clear()
        self.busy = False
        self._set_status("Listo", GREEN)
        threading.Thread(target=self.claude.reset, daemon=True).start()
        self._write("— Nueva conversación —\n", "meta")

    def close(self):
        self.claude.stop()

    # --- interno ---

    def _on_enter(self, event):
        if event.state & 0x1:  # Shift+Enter: salto de línea
            return None
        self._send_typed()
        return "break"

    def _send_typed(self):
        text = self.input.get("1.0", "end").strip()
        self.input.delete("1.0", "end")
        self.submit(text, "escrita")

    def _warm_up(self):
        try:
            self.claude.start()  # deja Claude abierto y listo para la primera orden
        except Exception as e:
            log.exception("no se pudo arrancar Claude")
            self.events.put(("answer", str(e), True))

    def _next(self):
        if self.busy or not self.pending:
            return
        self.busy = True
        self._set_status("Pensando…", ACCENT)
        try:
            self.claude.send(self.pending.pop(0))
        except Exception as e:
            log.exception("error al llamar a Claude")
            self.events.put(("answer", f"Error al llamar a Claude: {e}", True))

    def _poll(self):
        while True:
            try:
                msg, *args = self.events.get_nowait()
            except queue.Empty:
                break
            if msg == "progress":
                self._set_status(args[0], ACCENT)
            elif msg == "answer":
                text, is_error = args
                self._write("Claude: ", "meta")
                self._write(text.strip() + "\n", "error" if is_error else "claude")
                self._set_status("Error" if is_error else "Listo", RED if is_error else GREEN)
                self.busy = False
                self._next()
        self.win.after(100, self._poll)

    def _set_status(self, text, color):
        self.status.configure(text=f"●  {text}", text_color=color)

    def _write(self, text, tag):
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")
