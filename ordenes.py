"""Órdenes para Claude: las pasa a un `claude -p` que queda abierto y las muestra en un panel.

El proceso se arranca de antemano y sigue vivo entre órdenes (ahorra ~4 s por orden);
toda la conversación ocurre en él. Claude trabaja en la carpeta `claude/` (ver su
CLAUDE.md) y solo puede leer, buscar, abrir aplicaciones y anotar: todo lo demás lo
rechaza el modo "dontAsk".
"""

import json
import logging
import shutil
import subprocess
import threading
from pathlib import Path

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


# --- Chat -------------------------------------------------------------------

class ClaudeChat:
    """Lógica del panel de Claude (la ventana es ui/claude.html, en interfaz.py).
    Sus métodos públicos son los que puede llamar la página; lo interno empieza por "_"."""

    def __init__(self, ui):
        self._ui = ui                 # cola hacia la interfaz
        self._lock = threading.Lock()
        self._pending = []            # órdenes en cola mientras Claude trabaja
        self._busy = False
        self._claude = ClaudeSession(self._on_event)
        threading.Thread(target=self._warm_up, daemon=True).start()

    # --- llamadas desde la página ---

    def send(self, text):
        self.submit(text, "escrita")

    def new_conversation(self):
        with self._lock:
            self._pending.clear()
            self._busy = False
        threading.Thread(target=self._claude.reset, daemon=True).start()
        self._js("addMessage", "meta", "Nueva conversación")
        self._js("setStatus", "Listo", "ok")

    def close(self):
        self._ui.put(("hide_claude",))

    # --- desde Asistemis ---

    def submit(self, order, source):
        """Recibe una orden (source: "voz" o "escrita"), la anota y se la pasa a Claude."""
        order = order.strip()
        if not order:
            return
        save_note(order, tag=f"[orden {source}]")
        self._js("addMessage", "you", order, "Por voz" if source == "voz" else "")
        with self._lock:
            self._pending.append(order)
        self._next()

    def stop(self):
        self._claude.stop()

    # --- interno ---

    def _js(self, *call):
        self._ui.put(("chat", *call))

    def _warm_up(self):
        try:
            self._claude.start()  # deja Claude abierto y listo para la primera orden
        except Exception as e:
            log.exception("no se pudo arrancar Claude")
            self._js("addMessage", "error", str(e))

    def _next(self):
        with self._lock:
            if self._busy or not self._pending:
                return
            self._busy = True
            order = self._pending.pop(0)
        self._js("setStatus", "Pensando…", "busy")
        try:
            self._claude.send(order)
        except Exception as e:
            log.exception("error al llamar a Claude")
            self._on_event("answer", f"Error al llamar a Claude: {e}", True)

    def _on_event(self, kind, *args):
        if kind == "progress":
            self._js("setStatus", args[0], "busy")
        elif kind == "answer":
            text, is_error = args
            self._js("addMessage", "error" if is_error else "claude", text.strip())
            self._js("setStatus", "Error" if is_error else "Listo", "error" if is_error else "ok")
            with self._lock:
                self._busy = False
            self._next()
