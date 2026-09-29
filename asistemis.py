"""Asistemis: anotador por voz, gratis y local.

Di "Asistemis, anota <lo que sea>... eso es todo" y la nota se guarda con fecha
y hora en notas-asistemis.txt, en el escritorio. Ctrl+Alt+N empieza/termina
una nota sin decir la palabra de activación.
"""

import ctypes
import logging
import os
import queue
import re
import socket
import subprocess
import threading
import time
import tkinter as tk
import unicodedata
import wave
import winreg
from collections import deque
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pystray
import sounddevice as sd
from PIL import Image, ImageDraw
from pynput import keyboard

# --- Configuración ----------------------------------------------------------

WAKE_WORD = "asistemis"
WAKE_THRESHOLD = 0.85      # parecido mínimo (0-1) para aceptar la palabra de activación
WAKE_ALIASES = {"asisten"}  # cómo la oye a veces Whisper "base" (una nota vacía se descarta)
STOP_PHRASES = ("eso es todo", "eso seria todo")
SILENCE_SECONDS = 15       # cierra la nota tras este silencio aunque no se oiga "eso es todo"
MAX_SECONDS = 180          # corte de seguridad si no se oye "eso es todo"
PREROLL_SECONDS = 4.0      # audio previo a la detección que se incluye en la nota
CHECK_EVERY = 10           # bloques (1 s) entre escuchas de la palabra de activación / del final
CHECK_WINDOW = 30          # bloques (3 s) que se escuchan cada vez
SPEECH_LEVEL = 0.3         # volumen mínimo (0-1) para considerar que alguien habla
HOTKEY = "<ctrl>+<alt>+n"
WHISPER_MODEL = "large-v3-turbo"  # transcribe la nota (small se equivoca mucho con el micro del JBL)
LISTEN_MODEL = "base"      # más rápido, para escuchar continuamente
LOG_HEARD = True           # True: registra en el log lo que oye (para ajustar la activación)

BASE = Path(__file__).resolve().parent
WHISPER_DIR = BASE / "models" / "whisper"
LOG_FILE = BASE / "asistemis.log"

SR = 16000
BLOCK = SR // 10           # 100 ms por bloque

log = logging.getLogger("asistemis")


def desktop_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0]))
    except OSError:
        return Path.home() / "Desktop"


NOTES_FILE = desktop_dir() / "notas-asistemis.txt"


# --- Texto ------------------------------------------------------------------

def fold(text):
    """Minúsculas y sin tildes, conservando la longitud (para poder cortar el original)."""
    return "".join(unicodedata.normalize("NFD", c)[0] for c in text.lower())


def words(text):
    return re.findall(r"[a-z0-9]+", fold(text))


def is_wake(chunk):
    # también acepta la frase entera deformada: "asistenza nota" ~ "asistemis anota"
    return chunk in WAKE_ALIASES or len(chunk) >= 6 and (SequenceMatcher(None, chunk, WAKE_WORD).ratio() >= WAKE_THRESHOLD
                                or SequenceMatcher(None, chunk, WAKE_WORD + "anota").ratio() >= 0.8)


def heard_wake(text):
    # puede llegar partido ("a sistemis"), así que se prueban uniones de 1-3 palabras
    w = words(text)
    return any(is_wake("".join(w[i:i + n])) for i in range(len(w)) for n in (1, 2, 3))


STOP_TARGETS = [p.replace(" ", "") for p in STOP_PHRASES]


def heard_stop(text):
    w = words(text)
    return any(SequenceMatcher(None, "".join(w[i:i + n]), target).ratio() >= 0.85
               for target in STOP_TARGETS for i in range(len(w)) for n in (2, 3, 4))


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


def extract_note(text):
    """'Asistemis, anota hacer tarea 1, eso es todo.' -> 'Hacer tarea 1'
    Tolera lo que Whisper suele oír mal: 'Asistemi zanato', 'eso que es todo'."""
    tokens = list(re.finditer(r"[a-z0-9]+", fold(text)))  # misma longitud que text
    # corta en la aparición más parecida a "eso es todo" / "eso sería todo" (2-4 palabras)
    end = len(text)
    best = max(((similar("".join(t.group() for t in tokens[i:i + n]), target), i)
                for target in STOP_TARGETS
                for i in range(len(tokens)) for n in (2, 3, 4) if i + n <= len(tokens)),
               key=lambda s: s[0], default=(0, 0))
    if best[0] >= 0.8:
        end = tokens[best[1]].start()
    tokens = [t for t in tokens if t.end() <= end]
    # quita el "Asistemis anota" inicial (1-3 palabras), aunque venga deformado
    start = 0
    heads = [(max(similar(head, WAKE_WORD + "anota"), similar(head, WAKE_WORD + "apunta"),
                  similar(head, WAKE_WORD)), n)
             for n in (1, 2, 3) if len(tokens) >= n
             for head in ["".join(t.group() for t in tokens[:n])]]
    score, n = max(heads, default=(0, 0))
    if score >= 0.75:
        start = tokens[n - 1].end()
        nxt = tokens[n].group() if len(tokens) > n else ""
        if re.match(r"(?:anot|apunt)", nxt) or (re.search(r"n[aeiou]t", nxt) and similar(nxt, "anota") >= 0.5):
            start = tokens[n].end()
    else:
        command = next((t for t in tokens[:3] if re.match(r"(?:anot|apunt)", t.group())), None)
        if command:
            start = command.end()
    note = text[start:end].strip(" \t\n,.;:¡!¿?-—'\"")
    note = re.sub(r"[\s,;.]+(?:y|y bueno|bueno)$", "", note, flags=re.I)  # "... y bueno, eso es todo"
    return note[:1].upper() + note[1:]


def save_note(note):
    with NOTES_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%d/%m/%Y %H:%M} — {note}\n")


def open_notes():
    NOTES_FILE.touch(exist_ok=True)
    subprocess.Popen(["notepad.exe", str(NOTES_FILE)])


def level(block):
    rms = np.sqrt(np.mean(block.astype(np.float32) ** 2)) / 32768
    return float(np.clip((20 * np.log10(rms + 1e-9) + 55) / 45, 0, 1))


# --- Motor de audio ---------------------------------------------------------

class Engine(threading.Thread):
    """Escucha el micrófono, detecta la activación y el final, y transcribe.

    Solo este hilo toca el estado; la ventana y el atajo le hablan por `commands`
    y él responde a la ventana por `ui`.
    """

    IDLE, RECORDING, TRANSCRIBING = "idle", "recording", "transcribing"

    def __init__(self, ui):
        super().__init__(daemon=True)
        self.ui = ui
        self.commands = queue.Queue()
        self.audio = queue.Queue()
        self.state = self.IDLE
        self.preroll = deque(maxlen=int(PREROLL_SECONDS * SR / BLOCK))
        self.chunks = []
        self.whisper = None
        self.whisper_ready = threading.Event()
        self.resample_from = None
        self.checking = threading.Event()  # hay una escucha en curso
        self.generation = 0                # cambia con cada estado: descarta escuchas viejas
        self.blocks_seen = 0

    def run(self):
        try:
            os.environ["HF_HUB_OFFLINE"] = "1"  # los modelos ya están descargados: no consultar internet
            from faster_whisper import WhisperModel
            self.listener = WhisperModel(LISTEN_MODEL, device="cpu", compute_type="int8",
                                         download_root=str(WHISPER_DIR))
            threading.Thread(target=self._load_whisper, args=(WhisperModel,), daemon=True).start()
            stream = self._open_stream()
        except Exception as e:
            log.exception("no se pudo iniciar")
            self.ui.put(("error", f"No se pudo iniciar: {e}"))
            return
        self.ui.put(("ready",))
        with stream:
            while self._handle_commands():
                try:
                    block = self._to_16k(self.audio.get(timeout=0.2))
                except queue.Empty:
                    continue
                self.blocks_seen += 1
                if LOG_HEARD:
                    self._log_level(block)
                if self.state == self.IDLE:
                    self.preroll.append(block)
                    self._maybe_check(list(self.preroll), "wake")
                elif self.state == self.RECORDING:
                    self.chunks.append(block)
                    self.ui.put(("level", level(block)))
                    now = time.monotonic()
                    if level(block) >= SPEECH_LEVEL:
                        self.last_voice = now
                    self._maybe_check(self.chunks, "stop")
                    if now - self.last_voice > SILENCE_SECONDS:
                        log.info("silencio: nota cerrada")
                        self._finish()
                    elif now - self.started > MAX_SECONDS:
                        log.info("tiempo máximo alcanzado")
                        self._finish()

    def _maybe_check(self, blocks, kind):
        """Cada segundo, si alguien habla, escucha los últimos 3 s en segundo plano.
        Para el final basta con que se haya hablado en esos 3 s: "eso es todo" suele
        decirse bajando la voz, justo antes de callarse."""
        if self.blocks_seen % CHECK_EVERY or self.checking.is_set():
            return
        window = blocks[-CHECK_WINDOW:]
        recent = window if kind == "stop" else window[-CHECK_EVERY:]
        if max(level(b) for b in recent) < SPEECH_LEVEL:
            return
        self.checking.set()
        audio = np.concatenate(window).astype(np.float32) / 32768
        threading.Thread(target=self._check, args=(audio, kind, self.generation), daemon=True).start()

    def _check(self, audio, kind, generation):
        try:
            prompt = "Asistemis." if kind == "wake" else "Y eso es todo."
            segments, _ = self.listener.transcribe(audio, language="es", beam_size=1, vad_filter=True,
                                                   initial_prompt=prompt, condition_on_previous_text=False)
            text = "".join(s.text for s in segments).strip()
            if text and LOG_HEARD:
                log.info("oído (%s): %s", kind, text)
            if text and (heard_wake if kind == "wake" else heard_stop)(text):
                self.commands.put((kind, generation))
        except Exception:
            log.exception("error al escuchar")
        finally:
            self.checking.clear()

    def _open_stream(self):
        def callback(indata, frames, time_info, status):
            self.audio.put(bytes(indata))

        try:
            return sd.RawInputStream(samplerate=SR, blocksize=BLOCK, dtype="int16",
                                     channels=1, callback=callback)
        except sd.PortAudioError:
            # el micrófono no acepta 16 kHz: se graba a su frecuencia y se remuestrea
            rate = int(sd.query_devices(kind="input")["default_samplerate"])
            self.resample_from = rate
            return sd.RawInputStream(samplerate=rate, blocksize=rate // 10, dtype="int16",
                                     channels=1, callback=callback)

    def _to_16k(self, data):
        x = np.frombuffer(data, np.int16)
        if self.resample_from:
            n = int(len(x) * SR / self.resample_from)
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)
        return x

    def _log_level(self, block):
        self.peak = max(getattr(self, "peak", 0.0), level(block))
        if self.blocks_seen % 30 == 0:  # cada 3 s
            if self.peak >= SPEECH_LEVEL:
                log.info("volumen máx. %.2f", self.peak)
            self.peak = 0.0

    def _handle_commands(self):
        """Procesa las órdenes pendientes; devuelve False al salir."""
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                return True
            if isinstance(cmd, tuple):  # resultado de una escucha: ("wake" | "stop", generación)
                cmd, generation = cmd
                if generation != self.generation:
                    continue
            if cmd == "quit":
                return False
            if cmd == "wake" and self.state == self.IDLE:
                log.info("palabra de activación detectada")
                self._start(list(self.preroll))
            elif cmd == "toggle" and self.state == self.IDLE:
                self._start([])
            elif cmd in ("toggle", "finish", "stop") and self.state == self.RECORDING:
                self._finish()
            elif cmd == "cancel" and self.state == self.RECORDING:
                self.ui.put(("cancelled",))
                self._idle()
            elif cmd == "done":
                self._idle()

    def _start(self, preroll):
        self.state = self.RECORDING
        self.generation += 1
        self.chunks = preroll
        self.started = self.last_voice = time.monotonic()
        self.ui.put(("listening",))

    def _finish(self):
        if not self.chunks:
            self.ui.put(("nothing",))
            self._idle()
            return
        self.state = self.TRANSCRIBING
        self.generation += 1
        self.ui.put(("transcribing",))
        audio = np.concatenate(self.chunks).astype(np.float32) / 32768
        self.chunks = []
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _idle(self):
        self.chunks = []
        self.preroll.clear()
        self.generation += 1
        self.state = self.IDLE

    def _load_whisper(self, WhisperModel):
        try:
            self.whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                                        download_root=str(WHISPER_DIR))
            log.info("whisper cargado")
        except Exception:
            log.exception("no se pudo cargar whisper")
        finally:
            self.whisper_ready.set()

    def _transcribe(self, audio):
        if LOG_HEARD:
            with wave.open(str(BASE / "ultima-nota.wav"), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(SR)
                f.writeframes((audio * 32768).astype(np.int16).tobytes())
        try:
            self.whisper_ready.wait()
            if self.whisper is None:
                raise RuntimeError("no se pudo cargar Whisper")
            segments, _ = self.whisper.transcribe(audio, language="es", beam_size=5, vad_filter=True,
                                                  without_timestamps=True)
            text = "".join(s.text for s in segments).strip()
            if LOG_HEARD:
                log.info("nota completa: %s", text)
            note = extract_note(text)
            if note:
                save_note(note)
                log.info("nota guardada")
                self.ui.put(("saved", note))
            else:
                self.ui.put(("nothing",))
        except Exception as e:
            log.exception("error al transcribir")
            self.ui.put(("error", str(e)))
        finally:
            self.commands.put("done")


# --- Ventana ----------------------------------------------------------------

BG, BORDER, FG, MUTED = "#1e1f24", "#3a3c44", "#ececef", "#9a9ca5"
ACCENT, RED, GREEN, BUTTON = "#4f8cff", "#ff5a5a", "#3ecf8e", "#2d2f36"
BARS = 36


class Widget:
    def __init__(self, root, engine, ui):
        self.root, self.engine, self.ui = root, engine, ui
        self.hide_job = None
        self.levels = deque([0.0] * BARS, maxlen=BARS)

        scale = root.winfo_fpixels("1i") / 96
        self.w, self.h = int(340 * scale), int(172 * scale)
        x = root.winfo_screenwidth() - self.w - int(24 * scale)
        y = root.winfo_screenheight() - self.h - int(72 * scale)
        root.geometry(f"{self.w}x{self.h}+{x}+{y}")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.96)
        root.configure(bg=BORDER)

        frame = tk.Frame(root, bg=BG, padx=16, pady=10)
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(frame, bg=BG)
        header.pack(fill="x")
        self.dot = tk.Label(header, text="●", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.dot.pack(side="left")
        tk.Label(header, text="Asistemis", bg=BG, fg=FG, font=("Segoe UI Semibold", 11)).pack(side="left", padx=6)

        self.status = tk.Label(frame, bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x", pady=(2, 4))

        self.canvas = tk.Canvas(frame, height=int(40 * scale), bg=BG, highlightthickness=0)
        self.canvas.pack(fill="x")

        self.note = tk.Label(frame, bg=BG, fg=FG, font=("Segoe UI", 10), anchor="w", justify="left",
                             wraplength=self.w - int(34 * scale))
        self.note.pack(fill="x")

        self.buttons = tk.Frame(frame, bg=BG)
        self.buttons.pack(side="bottom", fill="x")
        for text, cmd in (("Listo", "finish"), ("Cancelar", "cancel")):
            tk.Button(self.buttons, text=text, command=lambda c=cmd: engine.commands.put(c),
                      bg=BUTTON, fg=FG, activebackground=BORDER, activeforeground=FG, relief="flat",
                      bd=0, padx=12, pady=2, cursor="hand2", font=("Segoe UI", 9)).pack(side="right", padx=(6, 0))

        for widget in (frame, header, self.status, self.note):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag)

        self.set("Iniciando…", MUTED, buttons=False)
        self._show()
        self._poll()

    def _drag_start(self, e):
        self.drag_from = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag(self, e):
        dx, dy = self.drag_from
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _show(self):
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        self.root.deiconify()
        self.root.lift()

    def _hide_after(self, ms):
        self._show()
        self.hide_job = self.root.after(ms, self.root.withdraw)

    def set(self, status, color, note="", buttons=False):
        self.status.config(text=status)
        self.dot.config(fg=color)
        self.note.config(text=note if len(note) < 160 else note[:157] + "…")
        if buttons:
            self.buttons.pack(side="bottom", fill="x")
        else:
            self.buttons.pack_forget()

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        step = w / BARS
        for i, v in enumerate(self.levels):
            bh = max(2, v * h)
            x = i * step + step * 0.2
            c.create_rectangle(x, (h - bh) / 2, x + step * 0.6, (h + bh) / 2, fill=ACCENT, width=0)

    def _poll(self):
        redraw = False
        while True:
            try:
                msg, *args = self.ui.get_nowait()
            except queue.Empty:
                break
            if msg == "level":
                self.levels.append(args[0])
                redraw = True
            elif msg == "ready":
                self.set("Listo. Di «Asistemis, anota…»", GREEN)
                self._hide_after(3000)
            elif msg == "listening":
                self.levels.extend([0.0] * BARS)
                redraw = True
                self.set("Escuchando… di «eso es todo» para terminar", RED, buttons=True)
                self._show()
            elif msg == "transcribing":
                self.levels.extend([0.0] * BARS)
                redraw = True
                self.set("Transcribiendo…", ACCENT)
            elif msg == "saved":
                self.set("✓ Anotado", GREEN, note=args[0])
                self._hide_after(4000)
            elif msg == "nothing":
                self.set("No entendí nada, no se guardó", MUTED)
                self._hide_after(3000)
            elif msg == "cancelled":
                self.set("Cancelado", MUTED)
                self._hide_after(1200)
            elif msg == "error":
                self.set("Error", RED, note=args[0])
                self._hide_after(8000)
            elif msg == "quit":
                self.root.destroy()
                return
        if redraw:
            self._draw()
        self.root.after(40, self._poll)


# --- Arranque ---------------------------------------------------------------

def tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(79, 140, 255))
    for i, bh in enumerate((14, 26, 36, 26, 14)):
        x = 16 + i * 7
        d.rounded_rectangle((x, 32 - bh / 2, x + 4, 32 + bh / 2), radius=2, fill="white")
    return img


def main():
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")

    # una sola instancia: el puerto queda ocupado mientras Asistemis está abierto
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47651))
    except OSError:
        ctypes.windll.user32.MessageBoxW(None, "Asistemis ya está funcionando (icono junto al reloj).",
                                         "Asistemis", 0x40)
        return

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass

    ui = queue.Queue()
    engine = Engine(ui)
    root = tk.Tk()
    root.title("Asistemis")
    Widget(root, engine, ui)

    def quit_app():
        engine.commands.put("quit")
        ui.put(("quit",))

    tray = pystray.Icon("asistemis", tray_image(), "Asistemis", menu=pystray.Menu(
        pystray.MenuItem("Anotar ahora (Ctrl+Alt+N)", lambda: engine.commands.put("toggle"), default=True),
        pystray.MenuItem("Abrir notas", open_notes),
        pystray.MenuItem("Salir", quit_app),
    ))
    tray.run_detached()
    hotkeys = keyboard.GlobalHotKeys({HOTKEY: lambda: engine.commands.put("toggle")})
    hotkeys.start()
    engine.start()
    log.info("Asistemis iniciado")

    root.mainloop()
    hotkeys.stop()
    tray.stop()


if __name__ == "__main__":
    main()
