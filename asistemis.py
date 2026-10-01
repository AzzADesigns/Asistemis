"""Asistemis: anotador por voz, gratis y local, que también pasa órdenes a Claude.

Ctrl+Alt+N (pensado para un botón del mouse) lo enciende y lo apaga:
- Encendido: escucha siempre "Asistemis…" y hace lo que se le pide.
- Apagado: micrófono cerrado y modelos descargados; no consume nada.

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

from herramientas import (APP_DIR, DATA_DIR, FROZEN, HOTWORDS, NOTES_FILE, STATUSES, add_task, close_app, next_track, pause_music, play_music, prev_track,
                          delete_task, fold, installed_apps, move_task, open_app, save_note, web_search)
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
GPU_ECO_IDLE = 45          # s sin usar la GPU en modo eco antes de descargarla
GPU_MODES = ("auto", "eco", "cpu")  # ajustes.json → "gpu_mode"
LOG_HEARD = False          # se lee de ajustes.json ("registrar_lo_oido"): guarda en el log lo que oye
                           # y el audio de la última nota; útil para ajustar la activación, no por privacidad

# --- Dictado continuo (T5/T7) ---
DICTATION_SECONDS = 10.0   # ventana rodante de audio del dictado (~10 s; nunca se acumula ilimitado)
DICTATION_PARTIAL_MIN = 2.0  # segundos de audio nuevo mínimo para lanzar un parcial
DICTATION_PARTIAL_MAX = 3.0  # o cada tanto, aunque no haya silencio
# frases que terminan el dictado (sin wake word durante el modo); "listo" es corta y puede
# aparecer en el contenido: se acepta el riesgo de falso positivo a cambio de la comodidad
DICTATION_STOP_PHRASES = ("eso es todo", "eso seria todo", "detene", "detente", "listo",
                          "finaliza", "termina la transcripcion", "ya esta")

# --- Ayuda y preguntas -------------------------------------------------------
# "Asistemis, ¿cómo funciona la transcripción?" → respuesta local
# "Asistemis, ¿cómo detengo el dictado?" → cómo cortar
# Otras preguntas → Claude (si está disponible)
HELP = re.compile(
    r"(?:ayud\w*|help|instrucc\w*|tutorial|gu[ií]a|manual|"
    r"informaci[oó]n|qu[eé]\s+(?:comandos?|pod[eé]|hace|hay|uso|puedo)|"
    r"c[oó]mo\s+(?:funciona|uso|hago|se\s+hace|lo\s+hago)|"
    r"c[oó]mo\s+(?:deteng|deten|paro|par|cort|fren|termin|cierro|salgo|freno)|"
    r"c[oó]mo\s+(?:funciona\s+la\s+)?(?:transcripci\w*|dictad\w*)|"
    r"qu[eé]\s+puedo\s+decir|para\s+qu[eé]\s+sirve)"
)
QUESTION = re.compile(
    r"^(?:qu[eé]|c[oó]mo|por\s+qu[eé]|d[oó]nde|cu[aá]ndo|cu[aá]l|cu[aá]nt[oa]|qui[eé]n|"
    r"me\s+explic|explicame|puedes\s+decirme|sab[eé]s|para\s+qu[eé])"
)
LEAD = re.compile(r"(?:(?:por\s+favor|porfa|che|bueno|a\s+ver|eh|asistemis)\s+)*", re.I)

HELP_TEXT = {
    "general": (
        "Cómo funciona Asistemis",
        "Decí «Asistemis, …» y una acción.\n"
        "• Nota: «Asistemis, anota comprar pan… eso es todo»\n"
        "• Dictado largo: «Asistemis, mododictado» (ventana en vivo)\n"
        "• App: «Asistemis, abrime Chrome»\n"
        "• Tarea: «Asistemis, agregá la tarea terminar el informe»\n"
        "• Música: «Asistemis, reproducime música»\n"
        "• Pregunta o Claude: «Asistemis, ejecutá …»\n"
        "• Ayuda: «Asistemis, ¿cómo funciona?» o pestaña Ayuda\n"
        "Encender/apagar: Ctrl+Alt+N. Abrir ventana: icono junto al reloj."
    ),
    "dictado": (
        "Transcripción por voz (dictado)",
        "Iniciar: «Asistemis, mododictado» · «transcribí» · «empezá a dictar»\n"
        "o botón Dictado en el menú del icono junto al reloj.\n\n"
        "Hablá con normalidad: el texto aparece en la ventana «Transcribiendo…».\n"
        "No hace falta repetir «Asistemis» durante el dictado.\n\n"
        "CÓMO TERMINAR (importante):\n"
        "1. Decí «eso es todo» o «detené»\n"
        "2. Tocá el botón LISTO (guarda)\n"
        "3. Cancelar = descarta (no guarda)\n"
        "4. Ctrl+Alt+N guarda y sale\n\n"
        "Se guarda como nota con tag [dictado] en la pestaña Notas."
    ),
    "stop": (
        "Cómo se detiene la transcripción",
        "Durante el DICTADO:\n"
        "• Voz: «eso es todo» · «detené» · «listo»\n"
        "• Botón LISTO = guardar y cortar\n"
        "• Botón CANCELAR = descartar\n"
        "• Ctrl+Alt+N = guardar y apagar Asistemis\n\n"
        "Durante una NOTA («Asistemis, anota…»):\n"
        "• «eso es todo» o ~15 s de silencio\n\n"
        "El micrófono se cierra solo al apagar (Ctrl+Alt+N)."
    ),
    "notas": (
        "Notas por voz",
        "«Asistemis, anota comprar pan… eso es todo»\n"
        "También: «apunta …»\n"
        "La nota se guarda con fecha en la pestaña Notas.\n"
        "Corta sola tras ~15 s de silencio.\n"
        "Desde el menú del icono: «Anotar ahora» (sin decir Asistemis)."
    ),
    "tareas": (
        "Tareas",
        "«Asistemis, agregá la tarea terminar el informe»\n"
        "«estoy haciendo la tarea 3» → En progreso\n"
        "«terminé la tarea 3» → Finalizadas\n"
        "«marcá la tarea 3 como pendiente»\n"
        "«borrá la tarea 3»\n"
        "Tablero en la pestaña Tareas de la ventana principal."
    ),
    "apps": (
        "Abrir y cerrar apps",
        "«Asistemis, abrime Chrome» / «iniciá Discord» / «jugá al God of War»\n"
        "«Asistemis, cerrá Chrome»\n"
        "«Asistemis, busca recetas de pizza»\n"
        "Si ya estaba abierta, la trae al frente."
    ),
    "musica": (
        "Música",
        "«Asistemis, reproducime música»\n"
        "«poné música» / «quiero escuchar música»\n\n"
        "Controles:\n"
        "• Pausar: «detene la música» / «pausá» / «stop música»\n"
        "• Siguiente: «siguiente canción» / «el siguiente tema»\n"
        "• Anterior: «canción anterior» / «la anterior»\n\n"
        "Necesita YouTube Music instalada como app."
    ),
    "power": (
        "Encender y apagar",
        "Ctrl+Alt+N → enciende / apaga el micrófono.\n"
        "Al apagar, los modelos se descargan de la RAM.\n"
        "Icono junto al reloj: doble clic abre la ventana;\n"
        "clic derecho = menú (Anotar, Dictado, Ayuda, Salir)."
    ),
}


def help_toast(topic):
    return HELP_TEXT.get(topic, HELP_TEXT["general"])


def help_or_question(text, start, end):
    """('help', tema) o ('question', frase) si lo dicho es ayuda o una pregunta.
    No roba órdenes reales (anota/abrime/buscá/…)."""
    seg = fold(text[start:end]).strip(" ,.;:¡!¿?-—'\"")
    if not seg:
        return None
    if re.match(r"(?:anot|apunt|abr|inici|arranc|lanz|jug|cerr|busc|ejecut|agreg|cre\w*|reproduc|pon[ea]|quiero\s+escuchar)", seg):
        return None
    lead = LEAD.match(seg)
    body = (seg[lead.end():] if lead else seg).strip(" ,.;:¡!¿?-—'\"")
    if not body:
        return None
    if HELP.match(body):
        if re.search(r"deten|parar|cortar|frenar|termin|listo|eso\s+es\s+todo|apag|encend|ctrl", body):
            return ("help", "stop")
        if re.search(r"dictad|transcri", body):
            return ("help", "dictado")
        if re.search(r"tarea", body):
            return ("help", "tareas")
        if re.search(r"musica|youtube", body):
            return ("help", "musica")
        if re.search(r"nota|anota|apunta", body):
            return ("help", "notas")
        if re.search(r"abr|cerr|app|chrome|discord|steam", body):
            return ("help", "apps")
        if re.search(r"encend|apag|ctrl|bot[oó]n|icono|bandeja", body):
            return ("help", "power")
        return ("help", "general")
    if QUESTION.match(body) and len(words(body)) >= 2:
        return ("question", body[:1].upper() + body[1:])
    return None


def question_local_answer(question):
    """Respuesta local si la pregunta es sobre Asistemis; si no, None (va a Claude)."""
    q = fold(question)
    # primero lo de "cómo lo detengo/corto" (aunque mencione "transcripción")
    if re.search(r"deten|parar|cortar|frenar|c[oó]mo\s+(?:paro|det|cort|fren)|terminar\s+el\s+dictado|c[oó]mo\s+se\s+termina", q):
        return HELP_TEXT["stop"]
    if re.search(r"dictad|transcri", q):
        return HELP_TEXT["dictado"]
    if re.search(r"tarea", q):
        return HELP_TEXT["tareas"]
    if re.search(r"musica|youtube", q):
        return HELP_TEXT["musica"]
    if re.search(r"nota|anota", q):
        return HELP_TEXT["notas"]
    if re.search(r"abr|app|chrome|discord|steam|busc", q):
        return HELP_TEXT["apps"]
    if re.search(r"encend|apag|ctrl|micro", q):
        return HELP_TEXT["power"]
    if re.search(r"asistemis|comando|funciona|uso|c[oó]mo\s+te", q):
        return HELP_TEXT["general"]
    return None

WHISPER_DIR = DATA_DIR / "models" / "whisper"
LOG_FILE = DATA_DIR / "asistemis.log"
SETTINGS_FILE = DATA_DIR / "ajustes.json"

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


DICTATION_STOP_TARGETS = [p.replace(" ", "") for p in DICTATION_STOP_PHRASES]


def heard_dictation_stop(text):
    """Frase que termina el modo dictado. Sin "Asistemis," al inicio; fuzzy como STOP_PHRASES.
    n incluye 1 para palabras sueltas ("listo", "detene")."""
    w = words(text)
    return any(SequenceMatcher(None, "".join(w[i:i + n]), target).ratio() >= 0.8
               for target in DICTATION_STOP_TARGETS for i in range(len(w)) for n in (1, 2, 3, 4, 5))


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


# qué se pide justo después de "Asistemis"
# "ejecutá <app o juego>" lo abre Asistemis; solo si no es una app se lo pasa a Claude
COMMANDS = (("note", r"(?:anot|apunt)"), ("open", r"(?:abr[ie]|inici|arranc|lanz|jug)"),
            ("close", r"(?:cerr|cier)"), ("search", r"busc"), ("order", r"ejecut"))
ACTIONS = ("open", "close", "search", "order", "task_add", "task_move", "music", "music_stop",
           "music_next", "music_prev")  # se ejecutan en cuanto hay una pausa
TASK_SILENCE = 1.5  # "agregá la tarea …" espera un poco más de silencio: la frase puede ser larga

# dictado continuo: frases que lo inician (T7). "anota" NO cuenta: eso sigue siendo nota normal.
DICTATION = re.compile(
    r"(?:transcrib\w*|dictad\w*|modo\s*dictado|"
    r"empez\w*\s+(?:a\s+)?(?:dictar|transcribir)|"
    r"inici\w*\s+(?:la\s+)?transcripci\w*)"
)

# tareas: "agregá la tarea comprar pan", "estoy haciendo la tarea 3", "finalicé la tarea tres"
NUMBER_WORDS = {w: i for i, w in enumerate(
    "cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis "
    "diecisiete dieciocho diecinueve veinte veintiuno veintidos veintitres veinticuatro veinticinco "
    "veintiseis veintisiete veintiocho veintinueve treinta".split())}
NUMBER_WORDS["una"] = 1
TASK_REF = re.compile(r"\btarea\s+(?:numero\s+|nro\.?\s*|n\s+|#\s*)?(\d+|" + "|".join(NUMBER_WORDS) + r")\b")
TASK_STATUS = (  # en este orden: "sacá la tarea 3" es borrar, no terminar
    ("borrar", r"\b(?:borr|elimin|sac)"),
    ("pendiente", r"\bpendiente"),
    ("hecha", r"\b(?:finali[zc]|termin|complet|hecha|hice|acab|list[ao]\b|cerr|cier)"),
    ("progreso", r"\b(?:progreso|haciendo|empec|empez|arranc|comenc|comienz|trabajando|curso)"),
)
TASK_ADD = re.compile(r"(?:agreg\w*|anot\w*|apunt\w*|cre\w*|nuev[ao])\s+(?:(?:la|una|otra)\s+)?(?:nueva\s+)?"
                      r"tareas?\b[\s,:]*(?:de\s+|que\s+)?")


# música: "reproducí música", "reproducime música / youtube", "quiero escuchar música", "quiero música"
MUSIC = re.compile(r"(?:reproduc\w*|quiero(?:\s+escuchar)?|pon[ea]\w*)\s+(?:(?:la|el|un[ao]?|algo\s+de|de)\s+)?"
                   r"(?:musica|youtube)\b")
# pausar / detener: "detene la música", "pausá", "pará el youtube", "stop música"
MUSIC_STOP = re.compile(
    r"(?:deteng\w*|deten\w*|par[aá]|parar|paus\w*|stop|fren\w*|silenci\w*|baj\w*|corta\w*)"
    r"(?:\s+(?:la|el|esa|esta|mi|tu|su|podes|podés|y|u))*"
    r"(?:\s+(?:musica|cancion|canción|youtube|reproductor|play|sonido|tema|esto|eso))?"
)


def music_command(text, start, end):
    """("music", "YouTube Music") si lo dicho es para poner música, o None."""
    return ("music", "YouTube Music") if MUSIC.match(fold(text[start:end]).lstrip(" ,.;:¡!¿?")) else None


def music_stop_command(text, start, end):
    """("music_stop", "YouTube Music") si lo dicho es para pausar/detener música, o None.
    No roba el dictado ni las notas: 'detene el dictado' / 'anota …' quedan fuera."""
    seg = fold(text[start:end]).strip(" ,.;:¡!¿?-—'\"")
    if not seg:
        return None
    if re.search(r"dictad|transcrib", seg):
        return None
    if re.match(r"(?:anot|apunt)", seg):
        return None
    # "pausa" / "stop" a secas: es media player, no otra cosa
    if re.match(r"(?:paus\w*|stop)\b", seg):
        return ("music_stop", "YouTube Music")
    if not MUSIC_STOP.match(seg):
        return None
    core = re.sub(r"^(?:deteng\w*|deten\w*|par[aá]|parar|paus\w*|stop|fren\w*|silenci\w*|baj\w*|corta\w*)",
                  "", seg).strip(" ,.;:¡!¿")
    if re.search(r"musica|cancion|youtube|reproductor|play|sonido|tema", core):
        return ("music_stop", "YouTube Music")
    if re.match(r"^(?:esto|eso)\b", core):
        return ("music_stop", "YouTube Music")
    return None


# siguiente / anterior pista: "siguiente canción", "el siguiente tema", "la anterior"
_SKIP_LEAD = r"(?:(?:por\s+favor|porfa|che|bueno|a\s+ver|eh|la|el|lo|las|los|una|un|otra|otro|mi|tu|su)\s+)*"
MUSIC_NEXT = re.compile(
    _SKIP_LEAD + r"(?:siguiente|proxim\w*|next)\b"
    r"(?:\s+(?:la|el|una|otra|cancion|canción|tema|pista|canci[oó]n|song))?"
    r"|" + _SKIP_LEAD + r"(?:(?:cancion|canción|tema|pista|canci[oó]n)\s+(?:siguiente|proxim\w*|next))"
)
MUSIC_PREV = re.compile(
    _SKIP_LEAD + r"(?:anterior|previ\w*|prev|atr[aá]s)\b"
    r"(?:\s+(?:la|el|una|otra|cancion|canción|tema|pista|canci[oó]n|song))?"
    r"|" + _SKIP_LEAD + r"(?:(?:cancion|canción|tema|pista|canci[oó]n)\s+(?:anterior|previ\w*|prev))"
)


def _music_skip_command(text, start, end):
    """("music_next"|"music_prev", "YouTube Music") o None. No roba dictado ni notas."""
    seg = fold(text[start:end]).strip(" ,.;:¡!¿?-—'\"")
    if not seg:
        return None
    if re.search(r"dictad|transcrib", seg) or re.match(r"(?:anot|apunt)", seg):
        return None
    if MUSIC_NEXT.match(seg):
        return ("music_next", "YouTube Music")
    if MUSIC_PREV.match(seg):
        return ("music_prev", "YouTube Music")
    return None


def dictation_command(text, start, end):
    """("dictation", "") si lo dicho es para iniciar el modo dictado, o None.
    Solo desde el camino con wake word: por botón ("Anotar ahora") no se activa dictado.
    El patrón debe estar al inicio del segmento (tolerando relleno): así
    "iniciá la transcripción" es dictado, pero "iniciá Chrome" u "hoy quiero transcribir X"
    no lo son. "anota transcripción …" sigue siendo nota normal."""
    seg = fold(text[start:end]).strip(" ,.;:¡!¿?-—'\"")
    if not seg:
        return None
    if re.match(r"(?:anot|apunt)", seg):
        return None
    lead = re.match(r"(?:(?:por\s+favor|porfa|che|bueno|a\s+ver|eh)\s+)*", seg)
    body = seg[lead.end():] if lead else seg
    return ("dictation", "") if DICTATION.match(body) else None


def command_of(token):
    return next((kind for kind, pattern in COMMANDS if re.match(pattern, token)), None)


def task_command(text, start, end, after_note=False):
    """("task_move", "3:progreso") / ("task_add", "Comprar pan") si lo dicho es sobre tareas, o None.
    after_note: "anota" ya se oyó pegado a "Asistemis" ("asistemisanota la tarea …")."""
    raw = text[start:end]
    lead = len(raw) - len(raw.lstrip(" ,.;:¡!¿?"))
    seg = ("anota " if after_note else "") + fold(raw[lead:])
    if after_note:
        lead -= len("anota ")
    ref = TASK_REF.search(seg)
    if ref and not re.match(r"(?:anot|apunt)", seg):
        status = next((name for name, pattern in TASK_STATUS if re.search(pattern, seg)), None)
        if status:
            number = ref.group(1)
            return "task_move", f"{int(number) if number.isdigit() else NUMBER_WORDS[number]}:{status}"
    add = TASK_ADD.match(seg)
    if add:
        body = raw[lead + add.end():].strip(" \t\n,.;:¡!¿?-—'\"")
        body = re.sub(r"[\s,;.]+(?:y|y bueno|bueno)$", "", body, flags=re.I)
        return "task_add", body[:1].upper() + body[1:]
    return None


def parse(text):
    """Separa qué se pide y el contenido:
    'Asistemis, anota hacer tarea 1, eso es todo.' -> ('note', 'Hacer tarea 1')
    'Asistemis, abrime el Chrome.'                 -> ('open', 'El Chrome')
    'Asistemis, buscá recetas de pizza.'           -> ('search', 'Recetas de pizza')
    'Asistemis, ejecuta busca X, eso es todo.'     -> ('order', 'Busca X')
    'Asistemis, agregá la tarea comprar pan.'      -> ('task_add', 'Comprar pan')
    'Asistemis, estoy haciendo la tarea 3.'        -> ('task_move', '3:progreso')
    'Asistemis, transcribí / modo dictado …'       -> ('dictation', '')
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
        if task := task_command(text, start, end, after_note=with_note):
            return task
        if not with_note and (music := music_command(text, start, end)):
            return music
        nxt = tokens[k].group() if len(tokens) > k else ""
        if with_note:  # "Asistemis anota" oído junto ("asisten sanota"): lo que sigue es la nota
            pass
        # dictado continuo (T7): antes de command_of, para que "iniciá la transcripción"
        # no caiga en open por el prefijo "inici". dictation_command exige el patrón al
        # inicio, así "buscá transcripción" / "abrime el dictado" siguen siendo órdenes.
        elif not with_note and (dictation := dictation_command(text, start, end)):
            return dictation
        # pausar música antes de ayuda: "detene la música" no debe ir a "cómo cortar"
        elif not with_note and (mstop := music_stop_command(text, start, end)):
            return mstop
        elif not with_note and (mskip := _music_skip_command(text, start, end)):
            return mskip
        # ayuda / preguntas: "¿cómo funciona la transcripción?", "¿qué comandos hay?"
        elif not with_note and (hq := help_or_question(text, start, end)):
            return hq
        elif command_of(nxt):
            kind, start = command_of(nxt), tokens[k].end()
        elif nxt == "a" and len(tokens) > k + 1 and command_of(nxt + tokens[k + 1].group()) == "note":
            start = tokens[k + 1].end()  # "a notar"
        elif re.search(r"n[aeiou]t", nxt) and similar(nxt, "anota") >= 0.5:
            start = tokens[k].end()
    elif task := task_command(text, 0, end):  # grabación con "Anotar ahora": "agregá la tarea …"
        return task
    elif music := music_command(text, 0, end):  # grabación con "Anotar ahora": "reproducime música"
        return music
    elif mstop := music_stop_command(text, 0, end):  # "detene la música" / "pausa"
        return mstop
    elif mskip := _music_skip_command(text, 0, end):  # "siguiente canción" / "la anterior"
        return mskip
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
        self.gpu_mode = load_settings().get("gpu_mode", "auto")  # auto | eco | cpu
        if self.gpu_mode not in GPU_MODES:
            self.gpu_mode = "auto"
        self.check_every = CHECK_EVERY
        self.gpu_idle_at = None  # momento en que se puede descargar la GPU (modo eco)
        self.probing = threading.Event()   # hay una transcripción de pausa en curso
        self.probed_at = None              # momento de voz que ya se transcribió en una pausa
        self.probe_offset = 0              # samples de chunks ya transcritos en sondas anteriores (T1)
        self.probe_text = ""               # texto acumulado de las sondas, para no re-transcribir al final (T1)
        self.recent_levels = deque(maxlen=CHECK_WINDOW)  # rms de los últimos N bloques: no recalcular (T4)
        # dictado continuo (T5): solo transcripción, sin wake word ni parse de órdenes
        self.dictating = False
        self.dictation_finishing = False  # True mientras se transcribe/guarda el cierre
        self.dictation_text = ""           # texto acumulado de los parciales (lo que ve la UI)
        self.dictation_offset = 0          # samples del dictado ya transcritos en dictation_text
        self.dictation_total = 0           # samples recibidos desde que empezó el dictado
        self.dictation_audio = deque(maxlen=int(DICTATION_SECONDS * SR / BLOCK))  # ventana rodante ~10 s
        self.dictation_probing = threading.Event()  # hay un parcial del dictado en curso
        self.dictation_partial_at = None   # momento del último parcial
        # encendido: escucha "Asistemis"; apagado: micrófono cerrado y modelos descargados
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
                self._release_gpu_eco()
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
                    if self.dictating:
                        self._dictation_block(block)
                        continue
                    self.chunks.append(block)
                    rms = level(block)  # se calcula una sola vez por bloque y se reutiliza (T4)
                    self.ui.put(("level", rms))
                    self.recent_levels.append(rms)
                    now = time.monotonic()
                    if rms >= SPEECH_LEVEL:
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
        if self.listener is None or (self.gpu and not self.whisper_ready.is_set()):
            return  # modelos descargados al apagar, o el modelo está volviendo a la GPU
        window = blocks[-CHECK_WINDOW:]
        recent = window if kind == "stop" else window[-self.check_every:]
        # T4: en RECORDING los rms ya están en recent_levels; en IDLE se recalcula (pocos bloques)
        if kind == "stop" and len(self.recent_levels) >= len(recent):
            speech = max(list(self.recent_levels)[-len(recent):]) >= SPEECH_LEVEL
        else:
            speech = max(level(b) for b in recent) >= SPEECH_LEVEL
        if not speech:
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
        """Tras una pausa, transcribe lo nuevo desde la última sonda (incremental, no todo el audio):
        si es una orden se ejecuta ya, sin esperar a "eso es todo". La ventana incluye 1 s de
        contexto al inicio para no cortar palabras en el borde."""
        if self.probing.is_set() or not (self.gpu or self.opening):
            return
        # en eco la sonda es más barata (beam 3) y puede faltar el turbo en la GPU
        beam = 3 if self.gpu_mode == "eco" else 5
        if self.gpu_mode == "eco" and (self.whisper is None or not self.gpu_loaded):
            self._ensure_gpu()
            return  # el turbo se está subiendo; la próxima pausa ya sondea
        self.probed_at = self.last_voice
        self.probing.set()
        start_sample = max(0, self.probe_offset - SR)  # 1 s de solape al inicio
        cum = 0
        start_idx = 0
        for i, block in enumerate(self.chunks):
            if cum + len(block) > start_sample:
                start_idx = i
                break
            cum += len(block)
        else:
            start_idx = len(self.chunks)
        if start_idx >= len(self.chunks):  # no hay audio nuevo desde la última sonda
            self.probing.clear()
            return
        audio = np.concatenate(self.chunks[start_idx:]).astype(np.float32) / 32768
        total_samples = sum(len(b) for b in self.chunks)  # foto al empezar la sonda
        threading.Thread(target=self._probe_run,
                         args=(audio, self.generation, self.last_voice, total_samples, beam),
                         daemon=True).start()

    def _probe_run(self, audio, generation, voice_at, new_offset, beam=5):
        try:
            self.whisper_ready.wait()
            if self.whisper is None:
                raise RuntimeError("no se pudo cargar Whisper")
            segments, _ = self.whisper.transcribe(audio, language="es", beam_size=beam, vad_filter=True, hotwords=HOTWORDS,
                                                  without_timestamps=True)
            text = "".join(s.text for s in segments).strip()
            if LOG_HEARD:
                log.info("pausa: %s", text)
            self.commands.put(("probe", generation, text, voice_at, new_offset))
        except Exception:
            log.exception("error al transcribir la pausa")
        finally:
            self.probing.clear()
            self._mark_gpu_busy()

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
                segment, voice_at, new_offset = rest
                self.probe_text = f"{self.probe_text} {segment}".strip() if self.probe_text else segment
                self.probe_offset = new_offset
                kind, body = parse(self.probe_text)  # se parsea el texto acumulado, no solo el segmento
                # dictado por voz (T7): solo con wake word, nunca desde "Anotar ahora"
                if kind == "dictation" and voice_at == self.last_voice and not self.by_button:
                    self._start_dictation()
                # solo si sigue callado desde entonces: si volvió a hablar, la orden no había terminado
                elif self.state == self.RECORDING and kind in ACTIONS and body and voice_at == self.last_voice:
                    if kind == "task_add" and time.monotonic() - self.last_voice < TASK_SILENCE:
                        self.probed_at = None  # todavía puede estar dictándola: se vuelve a mirar
                    else:
                        self._run_text(self.probe_text)
            elif cmd == "dictation_partial":
                if self.dictating and self.state == self.RECORDING:
                    segment, new_offset = rest
                    if segment:
                        self.dictation_text = (f"{self.dictation_text} {segment}".strip()
                                               if self.dictation_text else segment)
                    self.dictation_offset = new_offset
                    self.ui.put(("dictation_delta", self.dictation_text))
            elif cmd == "heard":
                if self.state == self.IDLE:
                    self.last_heard = rest[0]
                elif self.state == self.RECORDING and not self.dictating:
                    self.heard.append(rest[0])
                    self.opening = self._is_opening(self.heard)
            elif cmd == "quit":
                return False
            elif cmd == "power":
                if self.dictating:
                    # Ctrl+Alt+N durante el dictado: guarda por defecto y sale del modo
                    self._stop_dictation(save=True)
                else:
                    self._set_wake_by_voice(not self.wake_by_voice)
            elif cmd == "start_dictation":
                if self.state == self.RECORDING or self.state == self.IDLE:
                    self._start_dictation()
            elif cmd == "stop_dictation" and self.dictating:
                self._stop_dictation(save=True)
            elif cmd == "cancel_dictation" and self.dictating:
                self._stop_dictation(save=False)
            elif cmd == "dictation_stop" and self.dictating:
                self._stop_dictation(save=True)
            elif cmd == "wake" and self.state == self.IDLE:
                log.info("palabra de activación detectada")
                self._start(list(self.preroll), [self.last_heard])
            elif cmd == "toggle" and self.state == self.IDLE:
                log.info("grabación iniciada a mano")
                self._mic(True)
                self._start([], [], by_button=True)
            elif cmd in ("toggle", "finish", "stop") and self.state == self.RECORDING:
                if self.dictating:
                    self._stop_dictation(save=True)
                else:
                    self._finish()
            elif cmd == "cancel" and self.state == self.RECORDING:
                if self.dictating:
                    self._stop_dictation(save=False)
                else:
                    self.ui.put(("cancelled",))
                    self._idle()
            elif cmd == "done" and not self.dictating and not self.dictation_finishing:
                self._idle()

    def _start(self, preroll, heard, by_button=False):
        self._ensure_gpu()
        self.dictating = False  # una nota normal nunca hereda el modo dictado
        self.by_button = by_button  # con el botón, termina la segunda pulsación (no una pausa)
        self.state = self.RECORDING
        self.generation += 1
        self.chunks = preroll
        self.probe_text = ""
        self.probe_offset = 0
        self.recent_levels.clear()
        self.heard = heard
        self.opening = self._is_opening(heard)
        self.started = self.last_voice = time.monotonic()
        self.ui.put(("listening",))

    # --- Dictado continuo (T5) ---

    def _start_dictation(self):
        """Modo dictado: solo transcripción en vivo. Sin wake word, probe ni parse de órdenes.
        Si había una nota en curso se recicla limpio (generation nueva, buffers vacíos)."""
        log.info("dictado iniciado")
        if self.state == self.TRANSCRIBING:
            return  # no se interrumpe la transcripción de una nota u orden
        if not self.stream or not self.stream.active:
            self._mic(True)  # dictado desde el botón con el mic apagado
        self._ensure_gpu()
        self.dictating = True
        self.dictation_finishing = False
        self.by_button = False  # el dictado no termina con la segunda pulsación del botón
        self.state = self.RECORDING
        self.generation += 1
        self.chunks = []
        self.preroll.clear()
        self.probe_text = ""
        self.probe_offset = 0
        self.recent_levels.clear()
        self.heard = []
        self.opening = False
        self.dictation_text = ""
        self.dictation_offset = 0
        self.dictation_total = 0
        self.dictation_audio = deque(maxlen=int(DICTATION_SECONDS * SR / BLOCK))
        self.dictation_probing.clear()
        self.dictation_partial_at = None
        self.started = self.last_voice = time.monotonic()
        self.ui.put(("dictation_started",))
        # tip de corte en el widget rec (no abre la ventana principal)
        self.ui.put(("dictation_tip",))

    def _dictation_block(self, block):
        """Un bloque de audio durante el dictado: ventana rodante, nivel y parciales.
        No probe, no parse, no cierre por silencio ni por MAX_SECONDS."""
        self.dictation_audio.append(block)
        self.dictation_total += len(block)
        rms = level(block)
        self.ui.put(("level", rms))
        self.recent_levels.append(rms)
        now = time.monotonic()
        if rms >= SPEECH_LEVEL:
            self.last_voice = now
        self._maybe_check_dictation()
        silent = now - self.last_voice
        new_audio = self.dictation_total - self.dictation_offset
        min_new = int(DICTATION_PARTIAL_MIN * SR)
        if (not self.dictation_probing.is_set() and new_audio >= min_new
                and (silent >= COMMAND_SILENCE
                     or now - (self.dictation_partial_at or 0) >= DICTATION_PARTIAL_MAX)):
            self._dictation_partial()

    def _maybe_check_dictation(self):
        """Cada pocos bloques, si alguien habla, mira si pide terminar el dictado
        (sin wake word: las frases de fin no necesitan «Asistemis,»)."""
        if self.blocks_seen % self.check_every or self.checking.is_set():
            return
        if self.listener is None:
            return
        window = list(self.dictation_audio)[-CHECK_WINDOW:]
        if not window or max(level(b) for b in window) < SPEECH_LEVEL:
            return
        self.checking.set()
        audio = np.concatenate(window).astype(np.float32) / 32768
        threading.Thread(target=self._check_dictation, args=(audio, self.generation), daemon=True).start()

    def _check_dictation(self, audio, generation):
        try:
            segments, _ = self.listener.transcribe(audio, language="es", beam_size=1, vad_filter=True,
                                                   condition_on_previous_text=False)
            text = "".join(s.text for s in segments).strip()
            if text and LOG_HEARD:
                log.info("oído (dictado): %s", text)
            if text and heard_dictation_stop(text):
                self.commands.put(("dictation_stop", generation))
        except Exception:
            log.exception("error al escuchar el dictado")
        finally:
            self.checking.clear()

    def _dictation_partial(self):
        """Transcribe el audio nuevo desde el último parcial (incremental, sin parse).
        La ventana rodante (~10 s) garantiza que nunca se acumule audio ilimitado."""
        if self.dictation_probing.is_set():
            return
        start_sample = self.dictation_offset
        end_sample = self.dictation_total
        if end_sample - start_sample < int(0.4 * SR):  # demasiado poco: no se gasta CPU
            return
        audio = self._dictation_slice(start_sample, end_sample)
        if audio is None:
            return
        self.dictation_probing.set()
        self.dictation_partial_at = time.monotonic()
        threading.Thread(target=self._dictation_partial_run,
                         args=(audio, self.generation, end_sample), daemon=True).start()

    def _dictation_partial_run(self, audio, generation, new_offset):
        try:
            # CPU: si turbo aún no está listo, los parciales van con el listener (base);
            # no se bloquea el hilo principal esperando el modelo grande.
            model = None
            if self.whisper is not None and self.whisper_ready.is_set():
                model = self.whisper
            elif self.listener is not None:
                model = self.listener
            else:
                self.whisper_ready.wait()
                model = self.whisper if self.whisper is not None else self.listener
            if model is None:
                raise RuntimeError("no hay ningún modelo de Whisper disponible")
            beam = 1 if model is self.listener else 3  # parcial en vivo: ir rápido
            segments, _ = model.transcribe(audio, language="es", beam_size=beam, vad_filter=True,
                                           condition_on_previous_text=False)
            text = "".join(s.text for s in segments).strip()
            if LOG_HEARD:
                log.info("dictado parcial: %s", text)
            self.commands.put(("dictation_partial", generation, text, new_offset))
        except Exception:
            log.exception("error al transcribir el dictado")
        finally:
            self.dictation_probing.clear()

    def _dictation_slice(self, start_sample, end_sample):
        """Audio float32 del dictado entre muestras absolutas, solo si siguen en la ventana."""
        chunks = list(self.dictation_audio)
        held = sum(len(b) for b in chunks)
        oldest = self.dictation_total - held
        if end_sample <= oldest or start_sample >= self.dictation_total:
            return None
        take_from = max(start_sample, oldest)
        blocks = []
        cum = 0
        for block in chunks:
            block_start = oldest + cum
            cum += len(block)
            block_end = block_start + len(block)
            if block_end <= take_from or block_start >= end_sample:
                continue
            s = max(0, take_from - block_start)
            e = min(len(block), end_sample - block_start)
            blocks.append(block[s:e])
        if not blocks:
            return None
        return np.concatenate(blocks).astype(np.float32) / 32768

    def _stop_dictation(self, save=True):
        """Termina el modo dictado. save=True guarda como nota con tag [dictado]."""
        if not self.dictating:
            return
        log.info("dictado %s", "guardando" if save else "cancelando")
        self.dictating = False
        self.dictation_finishing = True
        self.state = self.TRANSCRIBING
        self.generation += 1  # descarta parciales en vuelo
        self.dictation_probing.clear()
        text = self.dictation_text
        offset = self.dictation_offset
        tail = self._dictation_slice(offset, self.dictation_total)
        self.dictation_audio.clear()
        self.dictation_text = ""
        self.dictation_offset = 0
        self.dictation_total = 0
        self.recent_levels.clear()
        if save and tail is not None:
            # transcribe la cola (lo no cubierto por los parciales) y suma al texto
            threading.Thread(target=self._dictation_tail_run,
                             args=(tail, text), daemon=True).start()
        else:
            self._dictation_commit(text, save)

    def _dictation_tail_run(self, audio, text_so_far):
        try:
            self.whisper_ready.wait()
            model = self.whisper if self.whisper is not None else self.listener
            if model is None:
                raise RuntimeError("no se pudo cargar Whisper")
            segments, _ = model.transcribe(audio, language="es", beam_size=5, vad_filter=True,
                                           without_timestamps=True)
            tail = "".join(s.text for s in segments).strip()
            text = f"{text_so_far} {tail}".strip() if tail else text_so_far
        except Exception as e:
            log.exception("error al transcribir el final del dictado")
            text = (text_so_far or "").strip()
            if not text:
                self.ui.put(("error", str(e)))
                self._idle()
                return
        self._dictation_commit(text, save=True)

    def _dictation_commit(self, text, save):
        try:
            text = (text or "").strip()
            if save and text:
                save_note(text, tag="[dictado]")
                log.info("dictado guardado (%s caracteres)", len(text))
                self.ui.put(("dictation_saved", text))
            elif save:
                self.ui.put(("nothing",))
            else:
                self.ui.put(("dictation_cancelled",))
        except Exception as e:
            log.exception("error al guardar el dictado")
            self.ui.put(("error", str(e)))
        finally:
            self._idle()

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
        self.probe_text = ""
        self.probe_offset = 0
        self.recent_levels.clear()
        threading.Thread(target=self._act, args=(text,), daemon=True).start()

    def _finish(self):
        if not self.chunks:
            self.ui.put(("nothing",))
            self._idle()
            return
        self.state = self.TRANSCRIBING
        self.generation += 1
        self.ui.put(("transcribing",))
        total_samples = sum(len(c) for c in self.chunks)
        probe_text, probe_offset = self.probe_text, self.probe_offset
        self.probe_text = ""
        self.probe_offset = 0
        self.recent_levels.clear()
        # T1: si las sondas ya cubrieron casi todo el audio, no se re-transcribe todo:
        # se transcribe solo la cola y se suma al texto acumulado
        if probe_text and total_samples and probe_offset >= total_samples * 0.9:
            cum = 0
            start_idx = 0
            for i, block in enumerate(self.chunks):
                if cum + len(block) > probe_offset:
                    start_idx = i
                    break
                cum += len(block)
            tail_blocks = self.chunks[start_idx:]
            self.chunks = []
            if tail_blocks:
                audio = np.concatenate(tail_blocks).astype(np.float32) / 32768
                threading.Thread(target=self._transcribe_tail, args=(audio, probe_text), daemon=True).start()
            else:
                log.info("nota completa desde las sondas")
                self._act(probe_text)
        else:
            audio = np.concatenate(self.chunks).astype(np.float32) / 32768
            self.chunks = []
            threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _idle(self):
        self.chunks = []
        self.preroll.clear()
        self.probe_text = ""
        self.probe_offset = 0
        self.recent_levels.clear()
        self.generation += 1
        self.state = self.IDLE
        self.by_button = False
        self.dictating = False
        self.dictation_finishing = False
        self.dictation_text = ""
        self.dictation_offset = 0
        self.dictation_total = 0
        self.dictation_audio.clear()
        self.dictation_probing.clear()
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
            if self.whisper is None or self.listener is None:
                # los modelos se descargaron de la RAM al apagar: recargarlos
                # (tradeoff: el primer arranque tras apagar es más lento)
                threading.Thread(target=self._load_models, daemon=True).start()
            else:
                self._ensure_gpu()
            self._mic(True)
        else:
            if self.dictating:  # apagar durante el dictado: guarda por defecto
                self._stop_dictation(save=True)
            elif self.state == self.RECORDING:  # apagar corta lo que se estaba grabando
                self._idle()
            elif self.state == self.IDLE:
                self._mic(False)
        self.ui.put(("mode", on))

    def _ensure_gpu(self):
        """Devuelve el modelo a la tarjeta gráfica (~0,7 s, mientras se empieza a hablar).
        En modo eco solo carga turbo si todavía no está (se descarga a los 45 s de quietud)."""
        if self.gpu_mode == "cpu" or not self.gpu:
            return
        self._mark_gpu_busy()
        if self.gpu_mode == "eco" and (self.whisper is None or not self.gpu_loaded):
            self.gpu_loaded = True
            self.whisper_ready.clear()
            threading.Thread(target=self._gpu_load, daemon=True).start()
            return
        if self.gpu and not self.gpu_loaded:
            self.gpu_loaded = True
            threading.Thread(target=self._gpu_load, daemon=True).start()

    def _gpu_load(self):
        try:
            from faster_whisper import WhisperModel
            if self.whisper is None:
                # modo eco: recién ahora se crea el turbo en la GPU
                self.whisper = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16",
                                            download_root=str(WHISPER_DIR), local_files_only=downloaded(WHISPER_MODEL))
                self.whisper.transcribe(np.zeros(SR, np.float32), language="es")
                log.info("turbo en la GPU (modo eco)")
            else:
                self.whisper.model.load_model()
                log.info("modelo de vuelta en la GPU")
        except Exception:
            log.exception("no se pudo volver a cargar el modelo")
            self.gpu_loaded = False
        finally:
            self.whisper_ready.set()
            self._mark_gpu_busy()

    def _maybe_unload(self):
        """Apagado, libera la memoria de los modelos en cuanto nada está usando uno.
        Al apagar de verdad (wake desactivado) se descargan también de la RAM de la CPU:
        large-v3-turbo int8 ocupa 1-2 GB que no deberían quedarse residentes para siempre.
        Tradeoff documentado: al reencender hay que recargar los modelos (arranque más lento).
        Si solo se cierra la UI pero Asistemis sigue escuchando (wake encendido), no se toca nada."""
        if not (self.state == self.IDLE and not self.wake_by_voice
                and self.whisper_ready.is_set() and not self.checking.is_set() and not self.probing.is_set()):
            return
        if self.gpu and self.gpu_loaded:
            self.whisper_ready.clear()
            self.whisper.model.unload_model(to_cpu=True)  # en GPU: primero a la RAM…
            self.gpu_loaded = False
            log.info("modelo fuera de la GPU")
        # …y al apagar del todo se descarga también de la RAM de la CPU (T2)
        if self.whisper is not None or self.listener is not None:
            self.whisper_ready.clear()
            self.whisper = None
            self.listener = None
            log.info("modelos descargados de la RAM")

    def _load_models(self):
        """Carga Whisper según ajustes.json → "gpu_mode":
        - auto: turbo float16 en la GPU para escuchar y transcribir (rápido, más VRAM).
        - eco:  wake en CPU (base); el turbo solo se sube a la GPU al dictar/nota y se
                descarga a los GPU_ECO_IDLE s de inactividad (menos consumo continuo).
        - cpu:  nunca toca la GPU (como no tener tarjeta NVIDIA)."""
        nvidia = APP_DIR / "nvidia" if FROZEN else Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
        for d in glob.glob(str(nvidia / "*" / "bin")):
            os.add_dll_directory(d)  # cuBLAS / cuDNN instalados con pip
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
        import ctranslate2
        from faster_whisper import WhisperModel
        mode = self.gpu_mode
        cuda = ctranslate2.get_cuda_device_count() > 0 and mode != "cpu"
        if cuda and mode == "auto":
            try:
                model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16",
                                     download_root=str(WHISPER_DIR), local_files_only=downloaded(WHISPER_MODEL))
                model.transcribe(np.zeros(SR, np.float32), language="es")  # calienta la GPU
                self.listener = self.whisper = model
                self.gpu, self.check_every, self.gpu_loaded = True, CHECK_EVERY_GPU, True
                self.whisper_ready.set()
                log.info("whisper cargado en la GPU (modo auto)")
                return
            except Exception:
                log.exception("no se pudo usar la GPU; sigo con la CPU")
        if cuda and mode == "eco":
            # wake barato en CPU; turbo se sube a la GPU solo cuando hace falta
            try:
                self.listener = WhisperModel(LISTEN_MODEL, device="cpu", compute_type="int8",
                                             download_root=str(WHISPER_DIR), local_files_only=downloaded(LISTEN_MODEL))
                self.gpu, self.gpu_loaded = True, False
                self.check_every = CHECK_EVERY  # el wake es en CPU: cada 1 s alcanza
                self.whisper = None
                self.whisper_ready.set()  # el listener ya está listo; turbo se carga after
                self.gpu_idle_at = None
                log.info("modo eco: wake en CPU; GPU solo al dictar o anotar")
                return
            except Exception:
                log.exception("no se pudo cargar el listener en CPU; sigo igual")
        self.listener = WhisperModel(LISTEN_MODEL, device="cpu", compute_type="int8",
                                     download_root=str(WHISPER_DIR), local_files_only=downloaded(LISTEN_MODEL))
        self.gpu = False
        self.check_every = CHECK_EVERY
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

    def set_gpu_mode(self, mode):
        """Cambia auto/eco/cpu. Guarda y recarga los modelos si el modo difiere."""
        if mode not in GPU_MODES:
            mode = "auto"
        self.gpu_mode = mode
        save_settings({**load_settings(), "gpu_mode": mode})
        log.info("gpu_mode = %s", mode)
        if self.state == self.IDLE and not self.checking.is_set() and not self.probing.is_set():
            self.whisper_ready.clear()
            if self.gpu and self.gpu_loaded:
                try:
                    self.whisper.model.unload_model(to_cpu=True)
                except Exception:
                    pass
                self.gpu_loaded = False
            self.whisper = None
            self.listener = None
            threading.Thread(target=self._load_models, daemon=True).start()

    def _release_gpu_eco(self):
        """Modo eco: descarga el turbo de la GPU si lleva GPU_ECO_IDLE s sin usarse."""
        if self.gpu_mode != "eco" or not self.gpu_loaded:
            return
        if self.state != self.IDLE or self.dictating or self.checking.is_set() or self.probing.is_set():
            self.gpu_idle_at = None
            return
        if self.gpu_idle_at is None:
            self.gpu_idle_at = time.monotonic() + GPU_ECO_IDLE
            return
        if time.monotonic() < self.gpu_idle_at:
            return
        try:
            self.whisper_ready.clear()
            if self.whisper is not None:
                self.whisper.model.unload_model(to_cpu=True)
                # en eco el turbo no hace falta en RAM: se recarga a demanda en la GPU
                self.whisper = None
            self.gpu_loaded = False
            self.gpu_idle_at = None
            log.info("modo eco: turbo fuera de la GPU (ahorro de VRAM)")
        except Exception:
            log.exception("no se pudo liberar la GPU (eco)")
        finally:
            self.whisper_ready.set()  # el wake sigue en CPU con listener

    def _mark_gpu_busy(self):
        if self.gpu_mode == "eco":
            self.gpu_idle_at = None

    def _wait_whisper(self, timeout=90):
        """En modo eco el turbo puede estar cargando en la GPU. Espera a que exista."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.whisper is not None:
                if self.gpu_mode != "eco" or self.gpu_loaded or not self.gpu:
                    return True
            time.sleep(0.4)
        return self.whisper is not None

    def _transcribe(self, audio):
        if LOG_HEARD:
            with wave.open(str(DATA_DIR / "ultima-nota.wav"), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(SR)
                f.writeframes((audio * 32768).astype(np.int16).tobytes())
        try:
            if self.gpu_mode == "eco" and (self.whisper is None or not self.gpu_loaded):
                self._ensure_gpu()
            if not self._wait_whisper():
                raise RuntimeError("no se pudo cargar Whisper")
            if self.whisper is None:
                raise RuntimeError("no se pudo cargar Whisper")
            beam = 3 if self.gpu_mode == "eco" else 5
            segments, _ = self.whisper.transcribe(audio, language="es", beam_size=beam, vad_filter=True, hotwords=HOTWORDS,
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

    def _transcribe_tail(self, audio, probe_text):
        """Transcribe solo el final no cubierto por las sondas y lo suma al texto acumulado."""
        try:
            self.whisper_ready.wait()
            if self.whisper is None:
                raise RuntimeError("no se pudo cargar Whisper")
            segments, _ = self.whisper.transcribe(audio, language="es", beam_size=5, vad_filter=True, hotwords=HOTWORDS,
                                                  without_timestamps=True)
            tail_text = "".join(s.text for s in segments).strip()
            text = f"{probe_text} {tail_text}".strip() if tail_text else probe_text
            if LOG_HEARD:
                log.info("nota incremental: %s", text)
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
            elif kind == "task_add":
                task_id = add_task(body)
                log.info("tarea #%s agregada", task_id)
                self.ui.put(("task_added", task_id, body))
            elif kind == "task_move":
                number, status = body.split(":")
                number = int(number)
                if status == "borrar":
                    done = delete_task(number)
                    self.ui.put(("task_deleted", number) if done else ("task_missing", number))
                elif task := move_task(number, status):
                    log.info("tarea #%s: %s", number, status)
                    self.ui.put(("task_moved", number, STATUSES[status], task["text"]))
                else:
                    self.ui.put(("task_missing", number))
            elif kind == "music":
                result = play_music()
                log.info("música: %s", result)
                if result in ("opened", "resumed", "playing"):
                    save_note(body, tag="[abrir]")
                self.ui.put(("music", result or "missing"))
            elif kind == "music_stop":
                result = pause_music()
                log.info("música pausa: %s", result)
                self.ui.put(("music_stop", result or "failed"))
            elif kind == "music_next":
                result = next_track()
                log.info("música siguiente: %s", result)
                self.ui.put(("music_next", result or "failed"))
            elif kind == "music_prev":
                result = prev_track()
                log.info("música anterior: %s", result)
                self.ui.put(("music_prev", result or "failed"))
            elif kind == "help":
                title, detail = help_toast(body or "general")
                log.info("ayuda: %s", body)
                self.ui.put(("help", title, detail))
            elif kind == "question":
                local = question_local_answer(body)
                if local:
                    log.info("pregunta local: %s", body)
                    self.ui.put(("help", local[0], local[1]))
                else:
                    log.info("pregunta → Claude: %s", body)
                    self.ui.put(("order", body))
            elif kind == "search":
                web_search(body)
                save_note(body, tag="[buscar]")
                log.info("búsqueda web")
                self.ui.put(("searched", body))
            elif kind == "order" and (result := open_app(body, min_score=0.85)):
                # "ejecutá Steam": es una app o un juego, no hace falta Claude (más exigente al
                # comparar, para no quedarse con órdenes de verdad que se parezcan a un nombre)
                app, how = result
                save_note(body, tag="[abrir]")
                log.info("%s: %s", "traída al frente" if how == "focused" else "abierta", app)
                self.ui.put((how, app))
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
    try:
        return Image.open(APP_DIR / "recursos" / "asistemis.png").resize((64, 64), Image.LANCZOS)
    except OSError:  # sin el icono: un círculo con barras de sonido
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((2, 2, 62, 62), fill=(20, 21, 26))
        for i, bh in enumerate((14, 26, 36, 26, 14)):
            x = 16 + i * 7
            d.rounded_rectangle((x, 32 - bh / 2, x + 4, 32 + bh / 2), radius=2, fill="white")
        return img


def listen_for_others(lock, ui):
    """Otra copia de Asistemis que se abrió (menú Inicio, doble clic) pide mostrar la ventana."""
    while True:
        try:
            conn, _ = lock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(2)
            try:
                if conn.recv(16) == b"mostrar":
                    ui.put(("main",))
            except OSError:
                pass


def main():
    # empaquetado como .exe sin consola no hay stdout/stderr: la barra de descarga de los modelos fallaría
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")

    # una sola instancia: el puerto queda ocupado mientras Asistemis está abierto
    # una sola instancia: el puerto queda ocupado mientras Asistemis está abierto. Si ya lo está,
    # abrirlo otra vez (menú Inicio, doble clic) solo le pide a esa instancia que muestre su ventana.
    background = "--segundo-plano" in sys.argv  # así arranca con Windows: sin abrir la ventana
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47651))
    except OSError:
        if not background:
            try:
                # esta copia la abrió el usuario: puede ceder el permiso de ponerse al frente
                ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
                with socket.create_connection(("127.0.0.1", 47651), timeout=2) as other:
                    other.sendall(b"mostrar")
            except OSError:
                pass
        return
    lock.listen(4)

    # identidad propia para Windows: "Asistemis", no "Python" (barra de tareas, notificaciones)
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Asistemis")
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
        pystray.MenuItem("Abrir Asistemis", lambda: ui.put(("main",)), default=True),
        pystray.MenuItem("Encendido (Ctrl+Alt+N)", lambda: engine.commands.put("power"),
                         checked=lambda _: engine.wake_by_voice),
        pystray.MenuItem("Anotar ahora", lambda: engine.commands.put("toggle")),
        pystray.MenuItem("Dictado", lambda: engine.commands.put("start_dictation")),
        pystray.MenuItem("Ayuda y comandos", lambda: ui.put(("help", "Ayuda", "Mirá la pestaña Ayuda de la ventana"))),
        pystray.MenuItem("Claude (Ctrl+Alt+C)", lambda: ui.put(("panel",))),
        pystray.MenuItem("Archivo de notas", open_notes),
        pystray.MenuItem("Salir", quit_app),
    ))
    tray.run_detached()
    hotkeys = Hotkeys({HOTKEY: lambda: engine.commands.put("power"),
                       PANEL_HOTKEY: lambda: ui.put(("panel",))})
    hotkeys.start()
    engine.start()
    log.info("Asistemis iniciado")
    threading.Thread(target=listen_for_others, args=(lock, ui), daemon=True).start()
    if not background:
        ui.put(("main",))

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
        subprocess.Popen(([sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())])
                         + ["--segundo-plano"], cwd=str(APP_DIR))
    logging.shutdown()
    os._exit(0)  # sin esperar a hilos que quedaron bloqueados


if __name__ == "__main__":
    main()
