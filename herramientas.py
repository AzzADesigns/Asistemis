"""Herramientas de Asistemis que también usa Claude (solo biblioteca estándar).

    python herramientas.py abrir "yt music"      abre una aplicación del menú Inicio (o trae la que ya está abierta)
    python herramientas.py cerrar "chrome"       cierra sus ventanas, como el botón ✕
    python herramientas.py anotar "comprar pan"  añade una nota al bloc de Asistemis
    python herramientas.py musica                abre YouTube Music (si hace falta) y pone la canción seleccionada

Solo abre aplicaciones instaladas (las de Get-StartApps) y nunca desinstaladores
ni herramientas del sistema, así que no sirve para ejecutar comandos arbitrarios.
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import winreg
import webbrowser
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

# El programa (código, ui/, claude/) puede estar en cualquier carpeta, también empaquetado como .exe;
# los datos del usuario (modelos de voz, ajustes, registro) van siempre a %LOCALAPPDATA%\Asistemis.
FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Asistemis"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def desktop_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0]))
    except OSError:
        return Path.home() / "Desktop"


NOTES_FILE = DATA_DIR / "notas.txt"  # se ven y se editan en la ventana de Asistemis
TASKS_FILE = DATA_DIR / "tareas.json"
SETTINGS_FILE = DATA_DIR / "ajustes.json"


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def get_theme():
    """Tema de la UI: 'dark' (por defecto), 'light' o 'system'."""
    theme = load_settings().get("theme", "dark")
    return theme if theme in ("dark", "light", "system") else "dark"


def set_theme(theme):
    if theme not in ("dark", "light", "system"):
        theme = "dark"
    save_settings({**load_settings(), "theme": theme})
    return theme


# --- Arranque con Windows -----------------------------------------------------
# La única fuente de verdad es la existencia del acceso directo en la carpeta de
# Inicio (misma carpeta que usa instalar.ps1 / compilar.ps1); no hay flag en ajustes.json.


def startup_dir():
    """Carpeta de Inicio de Windows (Startup) del usuario actual."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, "Startup")[0]))
    except OSError:
        return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_shortcut():
    """Ruta del acceso directo de arranque de Asistemis."""
    return startup_dir() / "Asistemis.lnk"


def autostart_enabled():
    """True si Asistemis arranca con Windows (existe el acceso directo de Inicio)."""
    return autostart_shortcut().exists()


def _autostart_command():
    """(target, arguments, workdir, icon_location) del acceso directo de arranque.
    Prefiere el .exe instalado; si no existe, pythonw + asistemis.py (mismo criterio que instalar.ps1)."""
    exe = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / "Asistemis" / "Asistemis.exe"
    if exe.exists():
        return str(exe), "--segundo-plano", str(exe.parent), f"{exe},0"
    pythonw = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable).parent / "pythonw.exe"
    script = APP_DIR / "asistemis.py"
    icon = APP_DIR / "recursos" / "asistemis.ico"
    icon_location = f"{icon},0" if icon.exists() else f"{pythonw},0"
    return str(pythonw), f'"{script}" --segundo-plano', str(APP_DIR), icon_location


def set_autostart(on):
    """Activa o desactiva el arranque con Windows creando/borrando el acceso directo de Inicio.
    Devuelve el estado resultante (True si el acceso directo existe)."""
    lnk = autostart_shortcut()
    if not on:
        try:
            lnk.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    target, arguments, workdir, icon_location = _autostart_command()
    # Los valores viajan como argumentos separados ($args[0..4]), sin interpolar rutas
    # dentro del script: así no hay problemas de comillas con espacios en las rutas.
    script = ("& { $lnk, $target, $arguments, $workdir, $icon = $args[0..4];"
              "$w = New-Object -ComObject WScript.Shell;"
              "$s = $w.CreateShortcut($lnk);"
              "$s.TargetPath = $target;"
              "$s.Arguments = $arguments;"
              "$s.WorkingDirectory = $workdir;"
              "$s.IconLocation = $icon;"
              "$s.Description = 'Asistemis: notas y órdenes por voz';"
              "$s.Save() }")
    subprocess.run(["powershell", "-NoProfile", "-Command", script,
                    str(lnk), target, arguments, workdir, icon_location],
                   capture_output=True, creationflags=NO_WINDOW)
    return lnk.exists()

# antes las notas estaban en el escritorio: si siguen ahí, pasan a los datos de Asistemis
_OLD_NOTES = desktop_dir() / "notas-asistemis.txt"
if _OLD_NOTES.exists() and not NOTES_FILE.exists():
    try:
        _OLD_NOTES.replace(NOTES_FILE)
    except OSError:
        pass


def save_note(note, tag=""):
    with NOTES_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%d/%m/%Y %H:%M} — {tag + ' ' if tag else ''}{note}\n")


NOTE_LINE = re.compile(r"(\d{2})/(\d{2})/(\d{4}) (\d{2}:\d{2}) — (?:\[([^\]]+)\] )?(.*)")


def read_notes():
    """Las notas del bloc, en orden: [{"i": línea, "date": "2026-09-29", "time": "20:31",
    "tag": "abrir" | "orden voz" | … | "", "text": …, "raw": línea tal cual}]."""
    try:
        lines = NOTES_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    notes = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = NOTE_LINE.match(line)
        if m:
            d, mo, y, time, tag, text = m.groups()
            notes.append({"i": i, "date": f"{y}-{mo}-{d}", "time": time, "tag": tag or "", "text": text, "raw": line})
        else:  # escrita a mano en el Bloc de notas
            notes.append({"i": i, "date": "", "time": "", "tag": "", "text": line, "raw": line})
    return notes


def delete_note(index, raw):
    """Borra la línea `index` del bloc, solo si sigue siendo la misma (por si el archivo cambió)."""
    lines = NOTES_FILE.read_text(encoding="utf-8").splitlines()
    if not (0 <= index < len(lines)) or lines[index] != raw:
        return False
    del lines[index]
    NOTES_FILE.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return True


def notes_version():
    """Cambia cada vez que se modifica el bloc (para refrescar la ventana)."""
    try:
        return NOTES_FILE.stat().st_mtime_ns
    except OSError:
        return 0


# --- Tareas -----------------------------------------------------------------

STATUSES = {"pendiente": "Pendiente", "progreso": "En progreso", "hecha": "Finalizada"}
_tasks_lock = threading.Lock()


def _load_tasks():
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("siguiente", 1)
    data.setdefault("tareas", [])
    return data


def _save_tasks(data):
    tmp = TASKS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TASKS_FILE)  # de una vez: nunca queda a medio escribir


def read_tasks():
    """[{"id": 3, "text": …, "status": "pendiente" | "progreso" | "hecha", "created": …, "updated": …}]"""
    with _tasks_lock:
        return _load_tasks()["tareas"]


def tasks_version():
    try:
        return TASKS_FILE.stat().st_mtime_ns
    except OSError:
        return 0


def add_task(text):
    """Nueva tarea pendiente; devuelve su número (no se reutiliza aunque se borre)."""
    with _tasks_lock:
        data = _load_tasks()
        task_id = data["siguiente"]
        now = datetime.now().isoformat(timespec="minutes")
        data["tareas"].append({"id": task_id, "text": text, "status": "pendiente", "created": now, "updated": now})
        data["siguiente"] = task_id + 1
        _save_tasks(data)
    save_note(text, tag=f"[tarea #{task_id}]")
    return task_id


def move_task(task_id, status):
    """Cambia el estado de la tarea; devuelve la tarea, o None si no existe."""
    if status not in STATUSES:
        return None
    with _tasks_lock:
        data = _load_tasks()
        task = next((t for t in data["tareas"] if t["id"] == task_id), None)
        if task and task["status"] != status:
            task["status"] = status
            task["updated"] = datetime.now().isoformat(timespec="minutes")
            _save_tasks(data)
            save_note(task["text"], tag=f"[tarea #{task_id} {status}]")
    return task


def delete_task(task_id):
    with _tasks_lock:
        data = _load_tasks()
        before = len(data["tareas"])
        data["tareas"] = [t for t in data["tareas"] if t["id"] != task_id]
        if len(data["tareas"]) != before:
            _save_tasks(data)
            return True
    return False


def fold(text):
    """Minúsculas y sin tildes, conservando la longitud (para poder cortar el original)."""
    return "".join(unicodedata.normalize("NFD", c)[0] for c in text.lower())


# --- Aplicaciones -----------------------------------------------------------

# nunca se abren: desinstaladores, consolas y herramientas que pueden romper el sistema
BLOCKED = re.compile(r"uninstall|desinstal|registr|regedit|recovery|recuperacion|cleanup|liberador|"
                     r"command prompt|simbolo del sistema|powershell|cmd|terminal|bash|shell|ejecutar|"
                     r"python|idle|node\.js|mysql|psql|git |diskpart|format|desfragment|memory|memoria|"
                     r"system configuration|configuracion del sistema|services|servicios|computer management|"
                     r"administracion de equipos|task scheduler|programador de tareas|herramientas de windows|"
                     r"copias de seguridad|firewall|odbc|iscsi|administrative|event viewer|visor de eventos|"
                     r"run$|wsl")

# cómo se suelen decir algunas aplicaciones
ALIASES = {
    "yt music": "YouTube Music", "youtube musica": "YouTube Music", "musica": "YouTube Music",
    "chrome": "Google Chrome", "google": "Google Chrome",
    # "navegador" NO va acá: se resuelve como el navegador predeterminado del sistema
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "vsc": "Visual Studio Code", "visual studio": "Visual Studio Code",
    "code": "Visual Studio Code", "explorador": "Explorador de archivos", "archivos": "Explorador de archivos",
    "riot": "Cliente de Riot", "epic": "Epic Games Launcher", "gog": "GOG GALAXY",
    "rockstar": "Rockstar Games Launcher", "ubisoft": "Ubisoft Connect", "edge": "Microsoft Edge",
    "bloc de notas": "Bloc de notas", "notepad": "Bloc de notas",
}

# cómo lo transcribe Whisper a veces ("abrime el IDE de Antigravity", "abrime estim", "Spoon")
ALIAS_PATTERNS = [
    (re.compile(r"anti\s*-?\s*gra|gravit"), "Antigravity IDE"),  # siempre el IDE, nunca el otro "Antigravity"
    (re.compile(r"\b(?:steam|e?st[ie]{1,2}[mn]|stim|spoon)\b"), "Steam"),
]

# "abrime el navegador" → el predeterminado del usuario (Chrome, Brave, Opera, Edge, Firefox…)
BROWSER_KEYS = {"navegador", "browser", "internet", "navegadordedefecto", "defaultbrowser",
                "elnavegador", "sunavegador", "mi navegador", "lanavegador"}


def default_browser_exe():
    """Ruta del navegador predeterminado de Windows (ProgId de http). Si no, None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice") as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0]
        for sub in (r"shell\open\command", r"shell\open\ddeexec"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog + "\\" + sub) as k:
                    cmd = winreg.QueryValueEx(k, None)[0]
                if not isinstance(cmd, str):
                    continue
                m = re.match(r'"([^"]+)"', cmd) or re.match(r'^\s*([A-Za-z]:\\[^\s"]+)', cmd)
                if m and Path(m.group(1)).exists():
                    return m.group(1)
            except OSError:
                continue
    except OSError:
        pass
    # fallbacks por si el ProgId no expone un .exe claro
    for raw in (
        r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
        r"%LocalAppData%\Programs\Opera\launcher.exe",
        r"%ProgramFiles%\Opera\launcher.exe",
        r"%LocalAppData%\Vivaldi\Application\vivaldi.exe",
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    ):
        p = Path(os.path.expandvars(raw))
        try:
            if p.exists():
                return str(p)
        except OSError:
            continue
    return None


def _normalize_query(query):
    """Limpia la orden sin aplicar aliases todavía (para detectar 'navegador' primero).
    También saca verbos de apertura: 'abrime el navegador' -> 'el navegador'."""
    q = fold(query).strip(" .,;:!?¡¿")
    verbs = re.compile(r"^(?:abr\w*|inici\w*|arranc\w*|lanz\w*|jug\w*|ejecut\w*|corre\w*|abro|abri|abrir)\s+")
    for _ in range(3):
        q = FILLER.sub("", q).strip()
        q = verbs.sub("", q).strip()
    return q


def _apply_aliases(q):
    q = ALIASES.get(q, q)
    return next((app for pattern, app in ALIAS_PATTERNS if pattern.search(q)), q)


def _is_browser_request(q):
    qk = _key(q)
    if not qk:
        return False
    if qk in BROWSER_KEYS:
        return True
    # "el navegador", "mi navegador", "navegador predeterminado"
    return qk.endswith("navegador") and len(qk) <= 14

# nombres que se le dan a Whisper como pista para que los escriba bien
HOTWORDS = "Asistemis, abrime Steam, Antigravity IDE, Figma, Discord, Chrome, YouTube Music, Spotify. Agregá la tarea. Reproducime música."

FILLER = re.compile(r"^(?:el|la|los|las|un|una|mi|me|al|a|por favor|porfa)\s+|\s+(?:por favor|porfa)$")

_apps, _apps_time = [], 0.0
_app_index = []          # [(nombre, app_id, key, word_keys)] precomputado al cargar
_app_by_key = {}         # key exacto -> primer (nombre, app_id)
_refreshing = threading.Lock()
_steam_cache, _steam_vdf_mtime = None, 0.0
_mru = []                # app_id abiertos hace poco (más probables)
_mru_time = {}
_FIND_CACHE = {}         # (query_key, min_score) -> (app, cuando)
_FIND_TTL = 45.0


def _menu_shortcuts():
    """Atajos del menú Inicio como (nombre, ruta .lnk). Cubre apps que Get-StartApps no lista
    (Chrome u otros instalados de forma que no aparecen en el índice del sistema)."""
    roots = []
    for var in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(var)
        if base:
            roots.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    out = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for lnk in root.rglob("*.lnk"):
                stem = lnk.stem
                if not stem or BLOCKED.search(fold(stem)):
                    continue
                out.append((stem, str(lnk)))
        except OSError:
            continue
    return out


def _common_exes():
    """Ejecutables habituales que a veces no están en el menú Inicio."""
    hints = [
        ("Google Chrome", r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        ("Google Chrome", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        ("Google Chrome", r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ("Mozilla Firefox", r"%ProgramFiles%\Mozilla Firefox\firefox.exe"),
        ("Mozilla Firefox", r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"),
        ("Visual Studio Code", r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe"),
        ("Discord", r"%LocalAppData%\Discord\Update.exe"),
        ("Spotify", r"%AppData%\Spotify\Spotify.exe"),
        ("Spotify", r"%LocalAppData%\Microsoft\WindowsApps\Spotify.exe"),
    ]
    out = []
    for name, raw in hints:
        p = Path(os.path.expandvars(raw))
        try:
            if p.exists():
                out.append((name, str(p)))
        except OSError:
            continue
    return out


def _load_apps():
    global _apps, _app_index, _app_by_key, _apps_time
    with _refreshing:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                              "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW).stdout
        data = json.loads(out or "[]")
        apps = [(a["Name"], a["AppID"]) for a in (data if isinstance(data, list) else [data])
                if not BLOCKED.search(fold(a["Name"]))]
        known = {_key(name) for name, _ in apps}
        extras = steam_games() + _menu_shortcuts() + _common_exes()
        for name, target in extras:
            k = _key(name)
            if k and k not in known and not BLOCKED.search(fold(name)):
                apps.append((name, target))
                known.add(k)
        _apps = apps
        _app_index, _app_by_key = _build_index(apps)
        _apps_time = time.monotonic()


def _build_index(apps):
    """Keys y palabras por app: no se recalculan en cada búsqueda."""
    index, by_key = [], {}
    for name, app_id in apps:
        nk = _key(name)
        words = [_key(w) for w in name.split() if len(w) > 3]
        index.append((name, app_id, nk, words))
        if nk and nk not in by_key:
            by_key[nk] = (name, app_id)
    return index, by_key


def steam_games():
    """Juegos instalados en Steam [(nombre, "steam://rungameid/<id>")].
    Se cachea: solo se releen los .acf si cambió libraryfolders.vdf."""
    global _steam_cache, _steam_vdf_mtime
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        mtime = vdf.stat().st_mtime
        if _steam_cache is not None and mtime == _steam_vdf_mtime:
            return _steam_cache
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _steam_cache or []
    games = []
    for library in re.findall(r'"path"\s+"([^"]+)"', text):
        folder = Path(library.replace("\\\\", "\\")) / "steamapps"
        for manifest in folder.glob("appmanifest_*.acf"):
            raw = manifest.read_text(encoding="utf-8", errors="replace")
            name, appid = re.search(r'"name"\s+"([^"]+)"', raw), re.search(r'"appid"\s+"(\d+)"', raw)
            if name and appid and not re.search(r"redistributable|proton|steam linux runtime|steamvr",
                                                name.group(1), re.I):
                games.append((name.group(1), f"steam://rungameid/{appid.group(1)}"))
    _steam_cache, _steam_vdf_mtime = games, mtime
    return games


def installed_apps():
    """[(nombre, AppID)] del menú Inicio, sin las bloqueadas. Tarda ~1 s en leerse, así que
    solo se espera la primera vez; después se refresca en segundo plano cada 10 min."""
    if not _apps:
        _load_apps()
    elif time.monotonic() - _apps_time > 600 and not _refreshing.locked():
        threading.Thread(target=_load_apps, daemon=True).start()
    return _apps


def _key(text):
    return re.sub(r"[^a-z0-9]", "", fold(text))


def _remember_open(app_id):
    now = time.monotonic()
    _mru[:] = [x for x in _mru if x != app_id]
    _mru.append(app_id)
    _mru_time[app_id] = now
    if len(_mru) > 24:
        old = _mru.pop(0)
        _mru_time.pop(old, None)


def _mru_boost(app_id):
    """Pequeño bonus a las apps abiertas hace poco (las más probables)."""
    try:
        i = _mru.index(app_id)
    except ValueError:
        return 0.0
    return 0.08 * (1.0 - i / max(len(_mru), 1))


def find_app(query, min_score=0.75):
    """Busca la aplicación más parecida a lo dicho ('abrime el chrome' -> Google Chrome).
    Orden: navegador predeterminado → alias exacto → key exacto → prefijo → fuzzy → MRU."""
    global _app_index, _app_by_key
    q = _normalize_query(query)
    apps = installed_apps()
    if not _app_index:
        _app_index, _app_by_key = _build_index(apps)
    # "abrime el navegador" → el del sistema (Brave, Opera, Edge, Chrome…) — antes de aliases
    if _is_browser_request(q):
        exe = default_browser_exe()
        if exe:
            return Path(exe).stem, exe
    q = _apply_aliases(q)
    qk = _key(q)
    if not qk:
        return None

    cache_key = (qk, min_score)
    hit = _FIND_CACHE.get(cache_key)
    if hit and time.monotonic() - hit[1] < _FIND_TTL:
        return hit[0]

    index = _app_index
    # 1) key exacto (O(1))
    if qk in _app_by_key:
        app = _app_by_key[qk]
        _FIND_CACHE[cache_key] = (app, time.monotonic())
        return app

    best, score = None, 0.0
    # 2) prefijo / contención barata + fuzzy con poda
    for name, app_id, nk, words in index:
        if not nk:
            continue
        s = 0.0
        if nk.startswith(qk) and len(qk) >= 4:
            s = 0.92
        elif qk.startswith(nk) and len(nk) >= 4:
            s = 0.85
        elif len(qk) >= 3 and qk in nk:
            s = 0.8
        else:
            sm = SequenceMatcher(None, qk, nk)
            # poda: si ni la ratio rápida llega al mínimo, no calculamos la exacta
            if sm.real_quick_ratio() < min_score - 0.15 and sm.quick_ratio() < min_score - 0.15:
                continue
            s = sm.ratio()
            for wk in words:
                if len(wk) < 4:
                    continue
                ws = SequenceMatcher(None, qk, wk).ratio() - 0.05
                if ws > s:
                    s = ws
        s += _mru_boost(app_id)
        if s > score:
            best, score = (name, app_id), s
    app = best if score >= min_score else None
    _FIND_CACHE[cache_key] = (app, time.monotonic())
    if len(_FIND_CACHE) > 200:
        _FIND_CACHE.clear()
    return app


# --- Ventanas abiertas --------------------------------------------------------

def _window_app_id(hwnd):
    """Identificador de app de una ventana (el mismo que usa el menú Inicio), si lo tiene."""
    from win32com.propsys import propsys, pscon
    try:
        store = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
        return store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue() or None
    except Exception:
        return None


def _window_exe(hwnd):
    import win32process
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf, size = ctypes.create_unicode_buffer(1024), ctypes.c_ulong(1024)
        ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return buf.value
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _norm_app_id(app_id):
    """Get-StartApps a veces devuelve '{GUID}\\Steam\\Steam.exe' (con \\ escapado)."""
    if not app_id:
        return ""
    return str(app_id).replace("\\\\", "\\")


def _app_folder(app_id):
    """Carpeta del programa si el AppID es una ruta ('{GUID}\\Steam\\Steam.exe' -> ...\\Steam)."""
    app_id = _norm_app_id(app_id)
    m = re.match(r"(\{[0-9A-Fa-f-]+\})\\(.+)", app_id)
    if not m and not re.match(r"[A-Za-z]:\\", app_id):
        return None
    try:
        if m:
            import pywintypes
            from win32com.shell import shell
            path = Path(shell.SHGetKnownFolderPath(pywintypes.IID(m.group(1)))) / m.group(2)
        else:
            path = Path(app_id)
        return str(path.parent).lower()
    except Exception:
        return None


def _resolve_app_target(app_id):
    """Devuelve una ruta de archivo real para abrir, o None si hay que usar AppsFolder."""
    app_id = _norm_app_id(app_id)
    if not app_id or "://" in app_id:
        return None
    # ruta de disco o UNC
    if re.match(r"[A-Za-z]:\\", app_id) or app_id.startswith("\\\\"):
        return app_id if Path(app_id).exists() else None
    # KnownFolder de Windows: {GUID}\Steam\Steam.exe
    m = re.match(r"(\{[0-9A-Fa-f-]+\})\\(.+)", app_id)
    if m:
        try:
            import pywintypes
            from win32com.shell import shell
            base = Path(shell.SHGetKnownFolderPath(pywintypes.IID(m.group(1))))
            target = base / m.group(2)
            return str(target) if target.exists() else None
        except Exception:
            return None
    return None


def open_app(query, min_score=0.75):
    """Si la app ya está abierta, trae su ventana; si no, la abre.
    Devuelve (nombre, "focused" | "opened"), o None si no hay ninguna parecida."""
    app = find_app(query, min_score)
    if not app:
        return None
    try:
        windows = app_windows(app)
    except Exception:
        windows = []
    if windows:
        _focus(windows[0])
        _remember_open(app[1])
        return app[0], "focused"
    target = _norm_app_id(app[1])
    if "://" in target:  # juego de Steam (steam://rungameid/...) u otro enlace
        os.startfile(target)
    else:
        resolved = _resolve_app_target(target)
        if resolved:
            os.startfile(resolved)
        else:
            # AUMID / AppID de Windows (incluye '{GUID}\App\app.exe'): AppsFolder
            ctypes.windll.shell32.ShellExecuteW(None, "open", f"shell:AppsFolder\\{target}", None, None, 1)
    _remember_open(app[1])
    return app[0], "opened"


def app_windows(app):
    """Ventanas abiertas de la app (hwnd), de la más reciente a la más vieja.
    Primero match barato (título / exe); el COM de AppUserModelID solo si hace falta.
    Una ventana con identificador propio de otra app nunca cuenta: así YouTube Music
    (que corre dentro de Chrome) no se confunde con Chrome."""
    import win32con
    import win32gui
    name, app_id = app
    folder = _app_folder(app_id)
    name_key = _key(name)
    app_id_l = (app_id or "").lower()
    cheap = []   # hwnds que calzan por título o exe
    unknown = [] # hwnds que podrían ser UWP / AppUserModelID

    def collect(hwnd, _):
        if (not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER)
                or win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or title.startswith("Asistemis"):
            return True
        exe = _window_exe(hwnd).lower()
        if ((folder and exe.startswith(folder + "\\")) or (exe and _key(Path(exe).stem) == name_key)
                or title == name or title.endswith(" - " + name)):
            cheap.append(hwnd)
        else:
            unknown.append((hwnd, title, exe))
        return True

    win32gui.EnumWindows(collect, None)
    found = list(cheap)
    # solo si el match barato no alcanzó, consultamos AppUserModelID (COM, más caro)
    if not found or len(found) < 2:
        for hwnd, title, exe in unknown:
            if hwnd in found:
                continue
            window_id = _window_app_id(hwnd)
            if window_id and window_id.lower() == app_id_l:
                found.append(hwnd)
            elif not window_id and folder and exe.startswith(folder + "\\"):
                found.append(hwnd)
    return found


def _focus(hwnd):
    """Trae la ventana al frente (Windows no deja hacerlo desde segundo plano sin este rodeo)."""
    import win32con
    import win32gui
    import win32process
    user32 = ctypes.windll.user32
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    foreground = user32.GetForegroundWindow()
    fg_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    if fg_thread and fg_thread != me:
        user32.AttachThreadInput(me, fg_thread, True)
    try:
        win32gui.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if fg_thread and fg_thread != me:
            user32.AttachThreadInput(me, fg_thread, False)


def close_app(query):
    """Cierra las ventanas de la app como el botón ✕ (si hay algo sin guardar, la app pregunta).
    Devuelve (nombre, ventanas cerradas), o None si no hay ninguna app parecida."""
    import win32con
    import win32gui
    app = find_app(query)
    if not app:
        return None
    windows = app_windows(app)
    for hwnd in windows:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    return app[0], len(windows)


# --- Música -------------------------------------------------------------------

MUSIC_APP = "YouTube Music"
PLAY_NAMES = ("Reproducir", "Play")   # botón de la barra del reproductor (según el idioma de YT Music)
PAUSE_NAMES = ("Pausar", "Pause", "Pausa")
# también botones que solo contienen la palabra (PWA de Brave/Chrome suelen variar)
_PAUSE_HINTS = ("paus", "pause")


def _buttons(window):
    """[(nombre, botón)] de la ventana: todos los botones de una vez (búsqueda nativa de
    UI Automation, ~0,1 s; recorrer el árbol desde Python tarda más de 1 s)."""
    import uiautomation as auto
    from uiautomation.uiautomation import _AutomationClient
    condition = _AutomationClient.instance().IUIAutomation.CreatePropertyCondition(
        auto.PropertyId.ControlTypeProperty, auto.ControlType.ButtonControl)
    found = window.Element.FindAll(4, condition)  # TreeScope_Descendants
    elements = [found.GetElement(i) for i in range(found.Length)]
    return [(e.CurrentName, e) for e in elements if e.CurrentName]


def _press_play(hwnd, timeout):
    """Toca «Reproducir» en la barra del reproductor sin traer la ventana al frente
    (UI Automation, como un lector de pantalla); cuando suena, el botón pasa a «Pausar».
    Si con eso no suena (recién abierta y sin canción en la barra), pone la primera de la
    página de inicio. Devuelve "playing" (ya sonaba), "played" o None si no pudo."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        window = auto.ControlFromHandle(hwnd)
        end, first_look = time.monotonic() + timeout, True
        while time.monotonic() < end:
            try:  # mientras la página carga, los botones aparecen y desaparecen
                buttons = _buttons(window)
                if any(name in PAUSE_NAMES for name, _ in buttons):
                    return "playing" if first_look else "played"
                first_look = False
                selected = [e for name, e in buttons if name in PLAY_NAMES]
                first = [e for name, e in buttons if name.startswith(PLAY_NAMES) and name not in PLAY_NAMES][:1]
                for element in selected + first:  # "Reproducir" y, si no alcanza, "Reproducir <lista>"
                    auto.Control.CreateControlFromElement(element).GetInvokePattern().Invoke()
                    for _ in range(12):
                        time.sleep(0.25)
                        if any(name in PAUSE_NAMES for name, _ in _buttons(window)):
                            return "played"
            except Exception:
                pass
            time.sleep(0.4)
    return None


def play_music():
    """«Reproducime música»: si YouTube Music está cerrada la abre y pone la canción
    seleccionada en cuanto carga; si está abierta, le da play (si ya sonaba, no la pausa).
    Devuelve "opened", "resumed", "playing" (ya sonaba) o "failed"; None si no está instalada."""
    app = find_app(MUSIC_APP)
    if not app:
        return None
    windows = app_windows(app)
    opened = not windows
    if opened:
        target = _norm_app_id(app[1])
        resolved = _resolve_app_target(target)
        if resolved:
            os.startfile(resolved)
        elif "://" in target:
            os.startfile(target)
        else:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"], creationflags=NO_WINDOW)
        end = time.monotonic() + 20
        while not windows and time.monotonic() < end:
            time.sleep(0.5)
            windows = app_windows(app)
        if not windows:
            return "failed"
    result = _press_play(windows[0], timeout=25 if opened else 8)
    if result == "played":
        return "opened" if opened else "resumed"
    return result or "failed"


def _media_key(vk):
    """Tecla multimedia del teclado (Play/Pause = 0xB3)."""
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def _find_pause_button(buttons):
    """Botón de pausa en la barra del reproductor (nombre exacto o que contenga 'paus')."""
    for name, el in buttons:
        if name in PAUSE_NAMES:
            return el
    for name, el in buttons:
        nl = (name or "").lower()
        if any(h in nl for h in _PAUSE_HINTS) and "reproduc" not in nl:
            # "Pausar", "Pausa la canción", etc.; no "Reproducir"
            return el
    return None


def _find_play_button(buttons):
    for name, el in buttons:
        if name in PLAY_NAMES:
            return el
    for name, el in buttons:
        if name and name.startswith(PLAY_NAMES) and name not in PAUSE_NAMES:
            if "reproduc" in name.lower() and len(name) <= 20:  # barra del player, no cada track
                return el
    return None


def pause_music():
    """Pausa YouTube Music (o lo que esté sonando).
    1) UI Automation: botón «Pausar» de la barra del reproductor.
    2) Si no aparece o ya está en pausa, tecla multimedia del sistema.
    Devuelve "paused", "already_paused", "not_open" o "failed"."""
    app = find_app(MUSIC_APP)
    windows = app_windows(app) if app else []
    import uiautomation as auto
    ui_result = None
    if windows:
        try:
            with auto.UIAutomationInitializerInThread():
                window = auto.ControlFromHandle(windows[0])
                buttons = _buttons(window)
                pause_el = _find_pause_button(buttons)
                if pause_el is not None:
                    auto.Control.CreateControlFromElement(pause_el).GetInvokePattern().Invoke()
                    time.sleep(0.35)
                    ui_result = "paused"
                elif _find_play_button(buttons) is not None:
                    ui_result = "already_paused"
        except Exception:
            ui_result = None
    if ui_result == "paused":
        return "paused"
    if ui_result == "already_paused" and not windows:
        return "not_open"
    # fallback / si la app no expone la barra: tecla multimedia del sistema
    try:
        _media_key(0xB3)  # VK_MEDIA_PLAY_PAUSE
        time.sleep(0.35)
        if ui_result == "already_paused":
            # UI decía play pero igual mandamos pause: puede estar sonando otra app
            return "paused"
        return "paused" if windows else "not_open"
    except Exception:
        return ui_result or ("failed" if windows else "not_open")


NEXT_NAMES = ("Siguiente", "Next", "Pista siguiente", "Canción siguiente", "Cancion siguiente")
PREV_NAMES = ("Anterior", "Previous", "Pista anterior", "Canción anterior", "Cancion anterior", "Atrás", "Atras")
_NEXT_HINTS = ("siguiente", "next")
_PREV_HINTS = ("anterior", "previous", "atrás", "atras")


def _find_player_button(buttons, exact, hints):
    for name, el in buttons:
        if name in exact:
            return el
    for name, el in buttons:
        nl = (name or "").lower()
        if not nl or "reproducir " in nl:  # tracks de la lista, no el player
            continue
        if any(h in nl for h in hints) and len(nl) <= 28:
            return el
    return None


def _media_key(vk):
    """Tecla multimedia del teclado (Play/Pause = 0xB3, Next = 0xB0, Prev = 0xB1)."""
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def _player_transport(direction):
    """Siguiente/anterior pista en YouTube Music.
    direction: "next" | "prev".
    1) Botón de la barra vía UI Automation.
    2) Fallback: tecla multimedia del sistema.
    Devuelve "ok" o "failed"."""
    app = find_app(MUSIC_APP)
    windows = app_windows(app) if app else []
    import uiautomation as auto
    exact = NEXT_NAMES if direction == "next" else PREV_NAMES
    hints = _NEXT_HINTS if direction == "next" else _PREV_HINTS
    if windows:
        try:
            with auto.UIAutomationInitializerInThread():
                window = auto.ControlFromHandle(windows[0])
                el = _find_player_button(_buttons(window), exact, hints)
                if el is not None:
                    auto.Control.CreateControlFromElement(el).GetInvokePattern().Invoke()
                    time.sleep(0.3)
                    return "ok"
        except Exception:
            pass
    vk = 0xB0 if direction == "next" else 0xB1  # VK_MEDIA_NEXT_TRACK / PREV_TRACK
    try:
        _media_key(vk)
        time.sleep(0.25)
        return "ok" if windows else "failed"
    except Exception:
        return "failed"


def next_track():
    """«Siguiente canción» / «siguiente tema»."""
    return _player_transport("next")


def prev_track():
    """«Canción anterior» / «tema anterior»."""
    return _player_transport("prev")


def web_search(query):
    """Abre la búsqueda en el navegador predeterminado."""
    webbrowser.open("https://www.google.com/search?q=" + quote_plus(query))


def main(argv):
    if argv[:1] == ["musica"]:
        result = play_music()
        print({"opened": "Abrí YouTube Music y puse la canción seleccionada.", "resumed": "Reproduciendo.",
               "playing": "Ya estaba sonando.", None: "YouTube Music no está instalada."}.get(
                   result, "No pude darle play en YouTube Music."))
        return 0 if result in ("opened", "resumed", "playing") else 1
    if len(argv) < 2 or argv[0] not in ("abrir", "cerrar", "anotar"):
        print(__doc__)
        return 2
    text = " ".join(argv[1:]).strip()
    if argv[0] == "anotar":
        save_note(text)
        print(f"Anotado en {NOTES_FILE}: {text}")
        return 0
    if argv[0] == "cerrar":
        result = close_app(text)
        if result and result[1]:
            print(f"Cerrado: {result[0]} ({result[1]} ventana/s)")
            return 0
        print(f"{result[0]} no está abierto." if result else f"No encontré ninguna aplicación parecida a «{text}».")
        return 1
    result = open_app(text)
    if result:
        print(("Traída al frente: " if result[1] == "focused" else "Abierto: ") + result[0])
        return 0
    print(f"No encontré ninguna aplicación parecida a «{text}». Instaladas: "
          + ", ".join(sorted({n for n, _ in installed_apps()})))
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
