"""Asistemis: anotador por voz, gratis y local, que también pasa órdenes a Claude.

Ctrl+Alt+N (pensado para un botón del mouse) lo enciende y lo apaga:
- Encendido: escucha siempre "Asistemis…" y hace lo que se le pide.
- Apagado: micrófono cerrado y modelo fuera de la GPU; no consume nada.

- "Asistemis, anota <lo que sea>… eso es todo": nota con fecha en notas-asistemis.txt (escritorio).
- "Asistemis, abrime Chrome" / "cerrá Chrome" / "busca <algo>" / "ejecuta <orden>": se hace en
  cuanto hay una pausa (abrir la app o traer la que ya está abierta, cerrarla, buscar en el
  navegador, o pasárselo a Claude: panel con Ctrl+Alt+C).
Las notas terminan con "eso es todo", "eso sería todo" o 15 s de silencio.
"""

import ctypes
import glob
import json
import logging
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from ctypes import wintypes
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pystray
import sounddevice as sd
from PIL import Image, ImageDraw

from herramientas import (HOTWORDS, NOTES_FILE, close_app, fold, installed_apps, open_app, save_note,
                          web_search)
from interfaz import Interface
from ordenes import ClaudeChat

# --- Configuración ----------------------------------------------------------

WAKE_WORD = "asistemis"
WAKE_THRESHOLD = 0.85      # parecido mínimo (0-1) para aceptar la palabra de activación
WAKE_ALIASES = {"asisten"}  # cómo la oye a veces Whisper "base" (una nota vacía se descarta)
STOP_PHRASES = ("eso es todo", "eso seria todo")
SILENCE_SECONDS = 15       # cierra la nota tras este silencio aunque no se oiga "eso es todo"
COMMAND_SILENCE = 0.8      # pausa tras la que se mira si lo dicho es una orden (y se ejecuta ya)
MAX_SECONDS = 180          # corte de seguridad si no se oye "eso es todo"
PREROLL_SECONDS = 4.0      # audio previo a la detección que se incluye en la nota
CHECK_EVERY = 10           # bloques (1 s) entre escuchas de la palabra de activación / del final (CPU)
CHECK_EVERY_GPU = 5        # con la tarjeta gráfica se puede escuchar cada 0,5 s
CHECK_WINDOW = 30          # bloques (3 s) que se escuchan cada vez
SPEECH_LEVEL = 0.3         # volumen mínimo (0-1) para considerar que alguien habla
HOTKEY = "N"               # Ctrl+Alt+N: enciende / apaga Asistemis (asígnalo a un botón del mouse)
PANEL_HOTKEY = "C"         # Ctrl+Alt+C: panel de Claude
WHISPER_MODEL = "large-v3-turbo"  # transcribe la nota (small se equivoca mucho con el micro del JBL)
LISTEN_MODEL = "base"      # sin tarjeta gráfica: más rápido, para escuchar continuamente
LOG_HEARD = False          # se lee de ajustes.json ("registrar_lo_oido"): guarda en el log lo que oye
                           # y el audio de la última nota; útil para ajustar la activación, no por privacidad

BASE = Path(__file__).resolve().parent
WHISPER_DIR = BASE / "models" / "whisper"
LOG_FILE = BASE / "asistemis.log"
SETTINGS_FILE = BASE / "ajustes.json"

SR = 16000
BLOCK = SR // 10           # 100 ms por bloque

log = logging.getLogger("asistemis")


# --- Texto ------------------------------------------------------------------

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


# qué se pide justo después de "Asistemis"
COMMANDS = (("note", r"(?:anot|apunt)"), ("open", r"abr[ie]"), ("close", r"(?:cerr|cier)"), ("search", r"busc"),
            ("order", r"ejecut"))
ACTIONS = ("open", "close", "search", "order")  # se ejecutan en cuanto hay una pausa


def command_of(token):
    return next((kind for kind, pattern in COMMANDS if re.match(pattern, token)), None)


def parse(text):
    """Separa qué se pide y el contenido:
    'Asistemis, anota hacer tarea 1, eso es todo.' -> ('note', 'Hacer tarea 1')
    'Asistemis, abrime el Chrome.'                 -> ('open', 'El Chrome')
    'Asistemis, buscá recetas de pizza.'           -> ('search', 'Recetas de pizza')
    'Asistemis, ejecuta busca X, eso es todo.'     -> ('order', 'Busca X')
    Tolera lo que Whisper suele oír mal ('Asistemi zanato', 'eso que es todo') y el
    audio previo a "Asistemis" que entra en la grabación."""
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
    # busca "Asistemis" (1-3 palabras, deformado o pegado a "anota") entre las primeras palabras
    heads = []
    for i in range(min(len(tokens), 10)):
        for n in (1, 2, 3):
            if i + n <= len(tokens):
                head = "".join(t.group() for t in tokens[i:i + n])
                with_note = max(similar(head, WAKE_WORD + "anota"), similar(head, WAKE_WORD + "apunta"))
                alone = max(similar(head, WAKE_WORD), 0.8 if head in WAKE_ALIASES else 0)
                heads.append((max(with_note, alone), -i, n, with_note > alone))
    score, i, n, with_note = max(heads, default=(0, 0, 0, False))
    kind, start = "note", 0
    if score >= 0.75:
        k = -i + n  # primera palabra después de "Asistemis"
        start = tokens[k - 1].end()
        nxt = tokens[k].group() if len(tokens) > k else ""
        if with_note:  # "Asistemis anota" oído junto ("asisten sanota"): lo que sigue es la nota
            pass
        elif command_of(nxt):
            kind, start = command_of(nxt), tokens[k].end()
        elif nxt == "a" and len(tokens) > k + 1 and command_of(nxt + tokens[k + 1].group()) == "note":
            start = tokens[k + 1].end()  # "a notar"
        elif re.search(r"n[aeiou]t", nxt) and similar(nxt, "anota") >= 0.5:
            start = tokens[k].end()
    elif tokens and command_of(tokens[0].group()):  # grabación con Ctrl+Alt+N: "ejecuta …"
        kind, start = command_of(tokens[0].group()), tokens[0].end()
    else:
        command = next((t for t in tokens[:3] if re.match(r"(?:anot|apunt)", t.group())), None)
        if command:
            start = command.end()
    body = text[start:end].strip(" \t\n,.;:¡!¿?-—'\"")
    body = re.sub(r"[\s,;.]+(?:y|y bueno|bueno)$", "", body, flags=re.I)  # "... y bueno, eso es todo"
    return kind, body[:1].upper() + body[1:]


def downloaded(model):
    """¿El modelo de Whisper ya está en models/whisper? Entonces no hace falta internet.
    Si no, se descarga la primera vez (turbo ~1,6 GB, base ~150 MB)."""
    return any(WHISPER_DIR.glob(f"models--*--faster-whisper-{model}/snapshots/*/model.bin"))


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


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
        self.last_heard = ""               # lo último que oyó la escucha de activación
        self.heard = []                    # lo que oye mientras graba
        self.opening = False               # la escucha ya oyó una orden ("abrime chrome")
        self.gpu = False
        self.check_every = CHECK_EVERY
        self.probing = threading.Event()   # hay una transcripción de pausa en curso
        self.probed_at = None              # momento de voz que ya se transcribió en una pausa
        # encendido: escucha "Asistemis"; apagado: micrófono cerrado y GPU libre
        self.wake_by_voice = load_settings().get("encendido", True)
        self.stream = None
        self.by_button = False
        self.gpu_loaded = False

    def run(self):
        try:
            threading.Thread(target=installed_apps, daemon=True).start()  # lista de apps, para abrir al instante
            self._load_models()
            stream = self._open_stream()
        except Exception as e:
            log.exception("no se pudo iniciar")
            self.ui.put(("error", f"No se pudo iniciar: {e}"))
            return
        self.stream = stream
        if self.wake_by_voice:
            stream.start()
        self.ui.put(("ready", self.wake_by_voice))
        try:  # sin "with": entrar en el bloque encendería el micrófono
            while self._handle_commands():
                self._maybe_unload()
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
                    silent = now - self.last_voice
                    if silent >= COMMAND_SILENCE and self.probed_at != self.last_voice and not self.by_button:
                        self._probe()
                    if silent > SILENCE_SECONDS:
                        log.info("silencio: nota cerrada")
                        self._finish()
                    elif now - self.started > MAX_SECONDS:
                        log.info("tiempo máximo alcanzado")
                        self._finish()
        finally:
            stream.close()

    def _maybe_check(self, blocks, kind):
        """Cada segundo, si alguien habla, escucha los últimos 3 s en segundo plano.
        Para el final basta con que se haya hablado en esos 3 s: "eso es todo" suele
        decirse bajando la voz, justo antes de callarse."""
        if self.blocks_seen % self.check_every or self.checking.is_set():
            return
        if self.gpu and not self.whisper_ready.is_set():  # el modelo está volviendo a la GPU
            return
        window = blocks[-CHECK_WINDOW:]
        recent = window if kind == "stop" else window[-self.check_every:]
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
            if text:
                self.commands.put(("heard", generation, text))
            if text and (heard_wake if kind == "wake" else heard_stop)(text):
                self.commands.put((kind, generation))
        except Exception:
            log.exception("error al escuchar")
        finally:
            self.checking.clear()

    def _probe(self):
        """Tras una pausa, transcribe lo dicho: si es una orden se ejecuta ya, sin esperar
        a "eso es todo". En la CPU tarda ~7 s, así que solo se hace si la escucha ya oyó la orden."""
        if self.probing.is_set() or not (self.gpu or self.opening):
            return
        self.probed_at = self.last_voice
        self.probing.set()
        audio = np.concatenate(self.chunks).astype(np.float32) / 32768
        threading.Thread(target=self._probe_run, args=(audio, self.generation, self.last_voice),
                         daemon=True).start()

    def _probe_run(self, audio, generation, voice_at):
        try:
            self.whisper_ready.wait()
            segments, _ = self.whisper.transcribe(audio, language="es", beam_size=5, vad_filter=True, hotwords=HOTWORDS,
                                                  without_timestamps=True)
            text = "".join(s.text for s in segments).strip()
            if LOG_HEARD:
                log.info("pausa: %s", text)
            self.commands.put(("probe", generation, text, voice_at))
        except Exception:
            log.exception("error al transcribir la pausa")
        finally:
            self.probing.clear()

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
            if isinstance(cmd, tuple):  # resultado de una escucha: ("wake" | "stop" | "heard", generación, …)
                cmd, generation, *rest = cmd
                if generation != self.generation:
                    continue
            if cmd == "probe":
                text, voice_at = rest
                kind, body = parse(text)
                # solo si sigue callado desde entonces: si volvió a hablar, la orden no había terminado
                if self.state == self.RECORDING and kind in ACTIONS and body and voice_at == self.last_voice:
                    self._run_text(text)
            elif cmd == "heard":
                if self.state == self.IDLE:
                    self.last_heard = rest[0]
                elif self.state == self.RECORDING:
                    self.heard.append(rest[0])
                    self.opening = self._is_opening(self.heard)
            elif cmd == "quit":
                return False
            elif cmd == "power":
                self._set_wake_by_voice(not self.wake_by_voice)
            elif cmd == "wake" and self.state == self.IDLE:
                log.info("palabra de activación detectada")
                self._start(list(self.preroll), [self.last_heard])
            elif cmd == "toggle" and self.state == self.IDLE:
                log.info("grabación iniciada a mano")
                self._mic(True)
                self._start([], [], by_button=True)
            elif cmd in ("toggle", "finish", "stop") and self.state == self.RECORDING:
                self._finish()
            elif cmd == "cancel" and self.state == self.RECORDING:
                self.ui.put(("cancelled",))
                self._idle()
            elif cmd == "done":
                self._idle()

    def _start(self, preroll, heard, by_button=False):
        self._ensure_gpu()
        self.by_button = by_button  # con el botón, termina la segunda pulsación (no una pausa)
        self.state = self.RECORDING
        self.generation += 1
        self.chunks = preroll
        self.heard = heard
        self.opening = self._is_opening(heard)
        self.started = self.last_voice = time.monotonic()
        self.ui.put(("listening",))

    @staticmethod
    def _is_opening(heard):
        """¿La escucha ya oyó una orden ("Asistemis, abrime <algo>")?"""
        kind, body = parse(" ".join(heard))
        return kind in ACTIONS and bool(body)

    def _run_text(self, text):
        """La transcripción de la pausa ya es la orden completa: se ejecuta sin volver a transcribir."""
        log.info("orden tras la pausa")
        self.state = self.TRANSCRIBING
        self.generation += 1
        self.chunks = []
        threading.Thread(target=self._act, args=(text,), daemon=True).start()

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
        self.by_button = False
        if not self.wake_by_voice:
            self._mic(False)

    def _mic(self, on):
        """Abre o cierra el micrófono (cerrado, Windows ni siquiera lo marca como en uso)."""
        if not self.stream or self.stream.active == on:
            return
        if on:
            while not self.audio.empty():  # descarta audio viejo
                self.audio.get_nowait()
            self.stream.start()
        else:
            self.stream.stop()

    def _set_wake_by_voice(self, on):
        self.wake_by_voice = on
        save_settings({**load_settings(), "encendido": on})
        log.info("Asistemis %s", "encendido" if on else "apagado")
        if on:
            self._ensure_gpu()
            self._mic(True)
        else:
            if self.state == self.RECORDING:  # apagar corta lo que se estaba grabando
                self._idle()
            elif self.state == self.IDLE:
                self._mic(False)
        self.ui.put(("mode", on))

    def _ensure_gpu(self):
        """Devuelve el modelo a la tarjeta gráfica (~0,7 s, mientras se empieza a hablar)."""
        if self.gpu and not self.gpu_loaded:
            self.gpu_loaded = True
            threading.Thread(target=self._gpu_load, daemon=True).start()

    def _gpu_load(self):
        try:
            self.whisper.model.load_model()
            log.info("modelo de vuelta en la GPU")
        except Exception:
            log.exception("no se pudo volver a cargar el modelo")
        finally:
            self.whisper_ready.set()

    def _maybe_unload(self):
        """Apagado, libera la memoria de la GPU en cuanto nada está usando el modelo."""
        if (self.gpu and self.gpu_loaded and not self.wake_by_voice and self.state == self.IDLE
                and self.whisper_ready.is_set() and not self.checking.is_set() and not self.probing.is_set()):
            self.whisper_ready.clear()
            self.whisper.model.unload_model(to_cpu=True)  # queda en la RAM: vuelve rápido
            self.gpu_loaded = False
            log.info("modelo fuera de la GPU")

    def _load_models(self):
        """Con tarjeta NVIDIA, un solo modelo "turbo" en la GPU escucha y transcribe (~0,3 s).
        Sin ella, "base" escucha en la CPU y "turbo" se carga aparte para las notas (~7 s)."""
        for d in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
            os.add_dll_directory(d)  # cuBLAS / cuDNN instalados con pip
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
        import ctranslate2
        from faster_whisper import WhisperModel
        if ctranslate2.get_cuda_device_count() > 0:
            try:
                model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16",
                                     download_root=str(WHISPER_DIR), local_files_only=downloaded(WHISPER_MODEL))
                model.transcribe(np.zeros(SR, np.float32), language="es")  # calienta la GPU
                self.listener = self.whisper = model
                self.gpu, self.check_every, self.gpu_loaded = True, CHECK_EVERY_GPU, True
                self.whisper_ready.set()
                log.info("whisper cargado en la GPU")
                return
            except Exception:
                log.exception("no se pudo usar la GPU; sigo con la CPU")
        self.listener = WhisperModel(LISTEN_MODEL, device="cpu", compute_type="int8",
                                     download_root=str(WHISPER_DIR), local_files_only=downloaded(LISTEN_MODEL))
        threading.Thread(target=self._load_whisper, args=(WhisperModel,), daemon=True).start()

    def _load_whisper(self, WhisperModel):
        try:
            self.whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                                        download_root=str(WHISPER_DIR), local_files_only=downloaded(WHISPER_MODEL))
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
            segments, _ = self.whisper.transcribe(audio, language="es", beam_size=5, vad_filter=True, hotwords=HOTWORDS,
                                                  without_timestamps=True)
            text = "".join(s.text for s in segments).strip()
            if LOG_HEARD:
                log.info("nota completa: %s", text)
        except Exception as e:
            log.exception("error al transcribir")
            self.ui.put(("error", str(e)))
            self.commands.put("done")
            return
        self._act(text)

    def _act(self, text):
        """Hace lo que se pidió: anotar, abrir, buscar o pasárselo a Claude."""
        try:
            kind, body = parse(text)
            if not body:
                self.ui.put(("nothing",))
            elif kind == "note":
                save_note(body)
                log.info("nota guardada")
                self.ui.put(("saved", body))
            elif kind == "open" and (result := open_app(body)):
                app, how = result
                save_note(body, tag="[abrir]")
                log.info("%s: %s", "traída al frente" if how == "focused" else "abierta", app)
                self.ui.put((how, app))
            elif kind == "close":
                result = close_app(body)
                log.info("cerrar %s: %s", body, result)
                if result and result[1]:
                    save_note(body, tag="[cerrar]")
                    self.ui.put(("closed", result[0]))
                else:  # no se le pasa a Claude: no hay nada que cerrar
                    self.ui.put(("not_open", result[0] if result else body))
            elif kind == "search":
                web_search(body)
                save_note(body, tag="[buscar]")
                log.info("búsqueda web")
                self.ui.put(("searched", body))
            else:  # orden, o una aplicación que no se encontró: que se ocupe Claude
                order = body if kind == "order" else f"Abrime {body}"
                log.info("orden para Claude")
                self.ui.put(("order", order))
        except Exception as e:
            log.exception("error al ejecutar")
            self.ui.put(("error", str(e)))
        finally:
            self.commands.put("done")


# --- Atajos -----------------------------------------------------------------

class Hotkeys(threading.Thread):
    """Atajos Ctrl+Alt+<tecla> registrados en Windows. A diferencia de pynput, también
    reciben las teclas que envía otro programa (p. ej. un botón del mouse en Logi Options+)."""

    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY, WM_QUIT = 0x1, 0x2, 0x4000, 0x0312, 0x0012

    def __init__(self, bindings):
        super().__init__(daemon=True)
        self.bindings = list(bindings.items())  # [(tecla, función)]
        self.thread_id = None

    def run(self):
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        for i, (key, _) in enumerate(self.bindings, 1):
            if not user32.RegisterHotKey(None, i, self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT, ord(key)):
                log.error("Ctrl+Alt+%s ya lo usa otro programa", key)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY and 1 <= msg.wParam <= len(self.bindings):
                self.bindings[msg.wParam - 1][1]()
        for i in range(1, len(self.bindings) + 1):
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)


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

    global LOG_HEARD
    LOG_HEARD = load_settings().get("registrar_lo_oido", False)

    ui = queue.Queue()
    engine = Engine(ui)
    chat = ClaudeChat(ui)
    interface = Interface(engine, ui, chat)

    def quit_app():
        engine.commands.put("quit")
        ui.put(("quit",))

    tray = pystray.Icon("asistemis", tray_image(), "Asistemis", menu=pystray.Menu(
        pystray.MenuItem("Encendido (Ctrl+Alt+N)", lambda: engine.commands.put("power"),
                         checked=lambda item: engine.wake_by_voice, default=True),
        pystray.MenuItem("Anotar ahora", lambda: engine.commands.put("toggle")),
        pystray.MenuItem("Claude (Ctrl+Alt+C)", lambda: ui.put(("panel",))),
        pystray.MenuItem("Abrir notas", open_notes),
        pystray.MenuItem("Salir", quit_app),
    ))
    tray.run_detached()
    hotkeys = Hotkeys({HOTKEY: lambda: engine.commands.put("power"),
                       PANEL_HOTKEY: lambda: ui.put(("panel",))})
    hotkeys.start()
    engine.start()
    log.info("Asistemis iniciado")

    import webview
    webview.start(interface.start)  # hasta que se cierran las ventanas (Salir)
    chat.stop()
    hotkeys.stop()
    tray.stop()
    import interfaz
    if not interfaz.QUITTING.is_set():
        # la interfaz se cerró sola: mejor reiniciar Asistemis entero que dejarlo a medias
        log.error("la interfaz se cerró inesperadamente; reiniciando Asistemis")
        lock.close()
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(BASE))
    logging.shutdown()
    os._exit(0)  # sin esperar a hilos que quedaron bloqueados


if __name__ == "__main__":
    main()
