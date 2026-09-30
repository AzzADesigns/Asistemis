"""Herramientas de Asistemis que también usa Claude (solo biblioteca estándar).

    python herramientas.py abrir "yt music"      abre una aplicación del menú Inicio (o trae la que ya está abierta)
    python herramientas.py cerrar "chrome"       cierra sus ventanas, como el botón ✕
    python herramientas.py anotar "comprar pan"  añade una nota al bloc de Asistemis

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


NOTES_FILE = desktop_dir() / "notas-asistemis.txt"


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
    "chrome": "Google Chrome", "google": "Google Chrome", "navegador": "Google Chrome",
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

# nombres que se le dan a Whisper como pista para que los escriba bien
HOTWORDS = "Asistemis, abrime Steam, Antigravity IDE, Figma, Discord, Chrome, YouTube Music, Spotify."

FILLER = re.compile(r"^(?:el|la|los|las|un|una|mi|me|al|a|por favor|porfa)\s+|\s+(?:por favor|porfa)$")

_apps, _apps_time = [], 0.0
_refreshing = threading.Lock()


def _load_apps():
    global _apps, _apps_time
    with _refreshing:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                              "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW).stdout
        data = json.loads(out or "[]")
        apps = [(a["Name"], a["AppID"]) for a in (data if isinstance(data, list) else [data])
                if not BLOCKED.search(fold(a["Name"]))]
        known = {_key(name) for name, _ in apps}
        apps += [game for game in steam_games() if _key(game[0]) not in known]  # juegos sin acceso directo
        _apps = apps
        _apps_time = time.monotonic()


def steam_games():
    """Juegos instalados en Steam [(nombre, "steam://rungameid/<id>")], aunque no tengan acceso directo."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
        vdf = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    games = []
    for library in re.findall(r'"path"\s+"([^"]+)"', vdf):
        folder = Path(library.replace("\\\\", "\\")) / "steamapps"
        for manifest in folder.glob("appmanifest_*.acf"):
            text = manifest.read_text(encoding="utf-8", errors="replace")
            name, appid = re.search(r'"name"\s+"([^"]+)"', text), re.search(r'"appid"\s+"(\d+)"', text)
            if name and appid and not re.search(r"redistributable|proton|steam linux runtime|steamvr",
                                                name.group(1), re.I):
                games.append((name.group(1), f"steam://rungameid/{appid.group(1)}"))
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


def find_app(query, min_score=0.75):
    """Busca la aplicación más parecida a lo dicho ('abrime el chrome' -> Google Chrome)."""
    q = fold(query).strip(" .,;:!?¡¿")
    for _ in range(3):
        q = FILLER.sub("", q).strip()
    q = ALIASES.get(q, q)
    q = next((app for pattern, app in ALIAS_PATTERNS if pattern.search(q)), q)
    apps = installed_apps()
    qk = _key(q)
    if not qk:
        return None
    best, score = None, 0.0
    for name, app_id in apps:
        nk = _key(name)
        s = SequenceMatcher(None, qk, nk).ratio()
        if nk == qk:
            s = 2.0
        elif nk.startswith(qk) and len(qk) >= 4:
            s = max(s, 0.9)
        # también contra cada palabra del nombre: "figma" ~ "Figma", "steam" ~ "Steam"
        s = max(s, max((SequenceMatcher(None, qk, _key(w)).ratio() for w in name.split() if len(w) > 3),
                       default=0) - 0.05)
        if s > score:
            best, score = (name, app_id), s
    return best if score >= min_score else None


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


def _app_folder(app_id):
    """Carpeta del programa si el AppID es una ruta ('{GUID}\\Steam\\Steam.exe' -> ...\\Steam)."""
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


def app_windows(app):
    """Ventanas abiertas de la app (nombre, AppID), de la más reciente a la más vieja.
    Se reconoce por su identificador de app, por la carpeta del programa o por el título
    ("… - Discord"). Una ventana con identificador propio de otra app nunca cuenta: así
    YouTube Music (que corre dentro de Chrome) no se confunde con Chrome."""
    import win32con
    import win32gui
    name, app_id = app
    folder = _app_folder(app_id)
    name_key = _key(name)
    found = []

    def check(hwnd, _):
        if (not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER)
                or win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or title.startswith("Asistemis"):
            return True
        window_id = _window_app_id(hwnd)
        if window_id:
            if window_id.lower() == app_id.lower():
                found.append(hwnd)
            elif not folder:
                return True  # es de otra app
        exe = _window_exe(hwnd).lower()
        if (folder and exe.startswith(folder + "\\")) or (exe and _key(Path(exe).stem) == name_key) \
                or title == name or title.endswith(" - " + name):
            if hwnd not in found:
                found.append(hwnd)
        return True

    win32gui.EnumWindows(check, None)
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
        return app[0], "focused"
    if "://" in app[1]:  # juego de Steam (steam://rungameid/...) u otro enlace
        os.startfile(app[1])
    else:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app[1]}"], creationflags=NO_WINDOW)
    return app[0], "opened"


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


def web_search(query):
    """Abre la búsqueda en el navegador predeterminado."""
    webbrowser.open("https://www.google.com/search?q=" + quote_plus(query))


def main(argv):
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
