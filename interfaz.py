"""Interfaz de Asistemis: ventanas HTML/CSS con cristal de Windows 11 (acrílico).

Cuatro ventanas sin marco, creadas al arrancar y ocultas hasta que hacen falta:
- toast:  "Asistemis encendido / apagado" (abajo a la derecha, se va sola).
- mic:    burbuja redonda al costado mientras está encendido, con el uso de la GPU.
- rec:    lo que se está grabando / transcribiendo / haciendo.
- claude: conversación con Claude.

Las ventanas se muestran y ocultan con llamadas de Windows que no roban el foco
(así no interrumpen un juego ni lo que estés escribiendo).
"""

import ctypes
import json
import logging
import queue
import threading
import time
from ctypes import wintypes
from pathlib import Path

import webview

log = logging.getLogger("asistemis")

UI_DIR = Path(__file__).resolve().parent / "ui"
user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi

# tamaños en píxeles CSS (se multiplican por la escala de Windows)
SIZES = {"toast": (340, 72), "mic": (64, 64), "rec": (380, 168), "claude": (440, 640)}
MARGIN = 16
TOAST_SECONDS = 2.6
GPU_EVERY = 1.0  # s entre lecturas del uso de la GPU (solo con la burbuja visible)

HWND_TOPMOST = wintypes.HWND(-1)
# tipos explícitos: en 64 bits, pasar -1 como int corrompe el identificador de ventana
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4


class MARGINS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def _dwm(hwnd, attr, value):
    v = ctypes.c_int(value)
    dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), attr, ctypes.byref(v), 4)


def work_area():
    """Zona útil de la pantalla principal, en píxeles reales. Si la barra de tareas se oculta
    sola, Windows no la descuenta; se reserva igual para que no tape las ventanas al aparecer."""
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    left, top, right, bottom = r.left, r.top, r.right, r.bottom
    taskbar = user32.FindWindowW("Shell_TrayWnd", None)
    if taskbar and user32.GetWindowRect(taskbar, ctypes.byref(r)):
        height = r.bottom - r.top
        if r.top >= bottom - 4 and height < (bottom - top) // 4:  # abajo y oculta
            bottom -= height
    return left, top, right, bottom


def page(name):
    """HTML de una ventana con la hoja de estilos común incrustada."""
    css = (UI_DIR / "base.css").read_text(encoding="utf-8")
    html = (UI_DIR / f"{name}.html").read_text(encoding="utf-8")
    return html.replace("/*BASE*/", css)


class Glass:
    """Una ventana webview con cristal: transparente, sin barra de tareas, siempre encima."""

    def __init__(self, name, js_api=None, focus=False, round_=False):
        self.name, self.round = name, round_
        self.hwnd = None
        self.visible = False
        self.ready = threading.Event()
        w, h = SIZES[name]
        self.win = webview.create_window(
            f"Asistemis {name}", html=page(name), js_api=js_api, width=w, height=h, x=-20000, y=-20000,
            frameless=True, easy_drag=False, on_top=True, transparent=True, focus=focus,
            resizable=False, min_size=(10, 10), shadow=False)
        self.win.events.loaded += self._on_loaded

    def _on_loaded(self):
        if self.ready.is_set():
            return
        import System
        import System.Drawing as D
        form = self.win.native

        def setup():
            self.scale = user32.GetDpiForWindow(form.Handle.ToInt32()) / 96
            w, h = SIZES[self.name]
            self.size = (round(w * self.scale), round(h * self.scale))
            form.MinimumSize = D.Size(1, 1)
            form.Size = D.Size(*self.size)
            form.BackColor = D.Color.Black  # negro = deja ver el material de Windows
            hwnd = self.hwnd = form.Handle.ToInt32()
            ex = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, (ex | 0x80) & ~0x40000)  # sin botón en la barra de tareas
            m = MARGINS(-1, -1, -1, -1)
            dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(m))
            _dwm(hwnd, 20, 0)   # tema claro
            if self.round:
                # el material de Windows siempre es rectangular: la burbuja es solo CSS sobre transparente
                _dwm(hwnd, 38, 1)
                _dwm(hwnd, 33, 1)
                _dwm(hwnd, 2, 1)   # sin sombra de ventana (DWMWA_NCRENDERING_POLICY = desactivada)
            else:
                _dwm(hwnd, 38, 3)  # material acrílico
                _dwm(hwnd, 33, 2)  # esquinas redondeadas
            user32.ShowWindow(hwnd, SW_HIDE)

        form.Invoke(System.Action(setup))
        self.ready.set()

    def place(self, x, y):
        self.pos = (int(x), int(y))
        if self.visible:
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, *self.pos, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

    def show(self, activate=False):
        self.ready.wait()
        self.visible = True
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, *self.pos, 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        if activate:
            user32.SetForegroundWindow(self.hwnd)

    def hide(self):
        if self.hwnd and self.visible:
            self.pos = self.current_pos()  # si la arrastraste, vuelve a salir ahí
            self.visible = False
            user32.ShowWindow(self.hwnd, SW_HIDE)

    def js(self, function, *args):
        """Llama a una función de la página: js("show", {...})."""
        self.ready.wait()
        try:
            self.win.evaluate_js(f"{function}({', '.join(json.dumps(a) for a in args)})")
        except Exception:
            log.exception("error en la interfaz (%s)", self.name)

    def current_pos(self):
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top


class GpuMeter:
    """Uso total de la GPU (NVIDIA) vía NVML: leerlo cuesta microsegundos."""

    def __init__(self):
        self.handle = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml = pynvml
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            log.info("sin lectura de GPU (NVML no disponible)")

    def percent(self):
        if not self.handle:
            return None
        try:
            return int(self.nvml.nvmlDeviceGetUtilizationRates(self.handle).gpu)
        except Exception:
            return None


class Interface:
    """Recibe los mensajes del motor (cola `ui`) y los muestra en las ventanas."""

    def __init__(self, engine, ui, chat_api):
        self.engine, self.ui = engine, ui
        self.toast = Glass("toast")
        self.mic = Glass("mic", js_api=MicApi(self), round_=True)
        self.rec = Glass("rec", js_api=RecApi(engine))
        self.chat = chat_api
        self.claude = Glass("claude", js_api=chat_api, focus=True)
        self.on = False
        self.hide_rec_at = None
        self.hide_toast_at = None
        self.gpu = GpuMeter()

    # --- arranque ---

    def start(self):
        """Se llama desde webview.start: coloca las ventanas y atiende la cola."""
        for g in (self.toast, self.mic, self.rec, self.claude):
            g.ready.wait()
        left, top, right, bottom = work_area()
        s = self.toast.scale
        m = round(MARGIN * s)
        self.toast.place(right - self.toast.size[0] - m, bottom - self.toast.size[1] - m)
        self.rec.place(right - self.rec.size[0] - m, bottom - self.rec.size[1] - m)
        self.mic.place(right - self.mic.size[0] - round(10 * s), top + (bottom - top) // 2 - self.mic.size[1] // 2)
        self.claude.place(right - self.claude.size[0] - m, top + m)
        threading.Thread(target=self._gpu_loop, daemon=True).start()
        self._loop()

    def _loop(self):
        levels = []
        while True:
            try:
                msg, *args = self.ui.get(timeout=0.08)
            except queue.Empty:
                msg, args = None, ()
            if msg == "level":
                levels.append(args[0])
            elif msg == "quit":
                for g in (self.toast, self.mic, self.rec, self.claude):
                    g.win.destroy()
                return
            elif msg:
                self._handle(msg, args)
            if levels and (msg is None or len(levels) >= 3):
                self.rec.js("levels", levels)
                levels = []
            self._timers()

    def _timers(self):
        now = time.monotonic()
        if self.hide_toast_at and now > self.hide_toast_at:
            self.hide_toast_at = None
            self.toast.js("leave")
            time.sleep(0.25)
            self.toast.hide()
            if self.on:
                self.mic.show()
        if self.hide_rec_at and now > self.hide_rec_at:
            self.hide_rec_at = None
            self.rec.js("leave")
            time.sleep(0.25)
            self.rec.hide()

    def _handle(self, msg, args):
        if msg in ("ready", "mode"):
            self.on = args[0]
            self.toast.js("show", self.on)
            self.toast.show()
            self.hide_toast_at = time.monotonic() + TOAST_SECONDS
            self.mic.js("state", "idle")
            if not self.on:
                self.mic.hide()
        elif msg == "panel":
            self.show_claude()
        elif msg == "hide_claude":
            self.claude.hide()
        elif msg == "chat":  # mensajes del chat con Claude
            self.claude.js(*args)
        else:
            self._rec(msg, args)

    def _rec(self, msg, args):
        """El widget de grabación: escuchando, transcribiendo y el resultado."""
        texts = {
            "listening": ("rec", "Escuchando", "Di «eso es todo» para terminar", None),
            "transcribing": ("busy", "Transcribiendo…", "", None),
            "saved": ("ok", "Anotado", args[0] if args else "", 4.0),
            "opened": ("ok", f"Abriendo {args[0]}" if args else "Abriendo", "", 2.5),
            "searched": ("ok", "Buscando en el navegador", args[0] if args else "", 2.5),
            "order": ("claude", "Enviado a Claude", args[0] if args else "", 2.5),
            "nothing": ("muted", "No entendí nada", "No se guardó nada", 3.0),
            "cancelled": ("muted", "Cancelado", "", 1.2),
            "error": ("error", "Error", args[0] if args else "", 8.0),
        }
        if msg not in texts:
            return
        kind, title, detail, seconds = texts[msg]
        self.rec.js("show", kind, title, detail)
        if not self.rec.visible:
            self.rec.show()
        self.mic.js("state", "rec" if kind in ("rec", "busy") else "idle")
        self.hide_rec_at = time.monotonic() + seconds if seconds else None
        if msg == "order":
            self.chat.submit(args[0], "voz")
            self.show_claude()

    def show_claude(self):
        self.claude.show(activate=True)
        self.claude.js("focusInput")

    def _gpu_loop(self):
        while True:
            time.sleep(GPU_EVERY)
            if self.mic.visible:
                value = self.gpu.percent()
                if value is not None:
                    self.mic.js("gpu", value)


class MicApi:
    """Lo que puede pedir la burbuja: doble clic abre Claude; se puede arrastrar."""

    def __init__(self, iface):
        self._iface = iface

    def open_claude(self):
        self._iface.show_claude()

    def drag(self, dx, dy):
        g = self._iface.mic
        x, y = g.current_pos()
        g.place(x + dx, y + dy)


class RecApi:
    """Botones del widget de grabación."""

    def __init__(self, engine):
        self._engine = engine

    def finish(self):
        self._engine.commands.put("finish")

    def cancel(self):
        self._engine.commands.put("cancel")
