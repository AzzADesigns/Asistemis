"""Herramientas de Asistemis que también usa Claude (solo biblioteca estándar).

    python herramientas.py abrir "yt music"      abre una aplicación del menú Inicio
    python herramientas.py anotar "comprar pan"  añade una nota al bloc de Asistemis

Solo abre aplicaciones instaladas (las de Get-StartApps) y nunca desinstaladores
ni herramientas del sistema, así que no sirve para ejecutar comandos arbitrarios.
"""

import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import winreg
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


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
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "visual studio": "Visual Studio Code",
    "code": "Visual Studio Code", "explorador": "Explorador de archivos", "archivos": "Explorador de archivos",
    "riot": "Cliente de Riot", "epic": "Epic Games Launcher", "gog": "GOG GALAXY",
    "rockstar": "Rockstar Games Launcher", "ubisoft": "Ubisoft Connect", "edge": "Microsoft Edge",
    "bloc de notas": "Bloc de notas", "notepad": "Bloc de notas",
}

FILLER = re.compile(r"^(?:el|la|los|las|un|una|mi|me|al|a|por favor|porfa)\s+|\s+(?:por favor|porfa)$")

_apps, _apps_time = [], 0.0


def installed_apps():
    """[(nombre, AppID)] del menú Inicio, sin las bloqueadas. Se refresca cada 10 min."""
    global _apps, _apps_time
    if not _apps or time.monotonic() - _apps_time > 600:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                              "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW).stdout
        data = json.loads(out or "[]")
        _apps = [(a["Name"], a["AppID"]) for a in (data if isinstance(data, list) else [data])
                 if not BLOCKED.search(fold(a["Name"]))]
        _apps_time = time.monotonic()
    return _apps


def _key(text):
    return re.sub(r"[^a-z0-9]", "", fold(text))


def find_app(query):
    """Busca la aplicación más parecida a lo dicho ('abrime el chrome' -> Google Chrome)."""
    q = fold(query).strip(" .,;:!?¡¿")
    for _ in range(3):
        q = FILLER.sub("", q).strip()
    q = ALIASES.get(q, q)
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
    return best if score >= 0.75 else None


def open_app(query):
    """Abre la aplicación y devuelve su nombre, o None si no hay ninguna parecida."""
    app = find_app(query)
    if app:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app[1]}"], creationflags=NO_WINDOW)
        return app[0]
    return None


def main(argv):
    if len(argv) < 2 or argv[0] not in ("abrir", "anotar"):
        print(__doc__)
        return 2
    text = " ".join(argv[1:]).strip()
    if argv[0] == "anotar":
        save_note(text)
        print(f"Anotado en {NOTES_FILE}: {text}")
        return 0
    name = open_app(text)
    if name:
        print(f"Abierto: {name}")
        return 0
    print(f"No encontré ninguna aplicación parecida a «{text}». Instaladas: "
          + ", ".join(sorted({n for n, _ in installed_apps()})))
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
