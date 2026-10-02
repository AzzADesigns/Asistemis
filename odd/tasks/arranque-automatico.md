# Feature: Arranque con Windows (ajuste)

## Objetivo
Agregar en Ajustes una opción para activar/desactivar que Asistemis se abra solo al iniciar Windows, y desactivar el arranque automático en esta PC.

## Problema / Por qué
El instalador crea un acceso directo en la carpeta de Inicio de Windows, pero no hay forma de cambiarlo desde la app. El usuario quiere controlarlo desde Ajustes y no quiere que abra al iniciar en esta PC.

## Alcance
- `herramientas.py`: helpers para detectar/crear/borrar el acceso directo de arranque (`Startup\Asistemis.lnk`).
- `interfaz.py`: API de la ventana principal (`autostart` / `set_autostart`).
- `ui/main.html`: sección "Arranque con Windows" en el modal Ajustes (Activado / Desactivado).
- `README.md`: mención de la nueva opción.
- En esta PC: borrar `Startup\Asistemis.lnk` (pedido explícito del usuario).

## Fuera de alcance
- Cambiar `instalar.ps1` / `compilar.ps1` (siguen creando el acceso como hoy).
- Mecanismo alterno por clave de registro `HKCU\...\Run`.
- Tareas programadas de Windows.

## Restricciones
- Una sola fuente de verdad: la existencia del acceso directo en la carpeta de Inicio (no un flag paralelo en `ajustes.json`).
- Al activar desde Ajustes: preferir el `.exe` instalado (`%LOCALAPPDATA%\Programs\Asistemis\Asistemis.exe`); si no existe, `pythonw` + `asistemis.py` del proyecto (mismo criterio que `instalar.ps1`).
- Argumentos del acceso: `--segundo-plano` (arranque sin abrir la ventana).
- Crear/borrar el `.lnk` vía PowerShell `WScript.Shell` (mismo mecanismo que `instalar.ps1`), sin dependencias nuevas.
- Texto de UI en español, con el estilo del modal Ajustes existente (filas `theme-row` / `theme-opt`).
- No romper: `theme`, `gpu_mode`, `MainApi` existente, PyInstaller, CLI de `herramientas.py`.

## Modo TDD
- Fuente: no hay tests en el repo (solo manual). Se verifica por sintaxis + revisión de flujo.
- Runner: N/A — `python -m py_compile` sobre los archivos tocados e inspección estructural.

## Entrega
- Estrategia: ask-on-risk por defecto.
- Candidato: work-unit commits en `feature/arranque-automatico`.

---

## Tareas

### T1 — Helpers de arranque en herramientas.py (P1)
- [x] `startup_dir()`, `autostart_shortcut()`, `autostart_enabled()`, `_autostart_command()`, `set_autostart(on)`
- [x] Crear/borrar el `.lnk` vía PowerShell WScript.Shell; devolver el estado resultante
- [x] Verificación: `python -m py_compile herramientas.py`

### T2 — API MainApi en interfaz.py (P1)
- [x] `autostart()` y `set_autostart(on)` delegando a `herramientas`
- [x] Verificación: `python -m py_compile interfaz.py`

### T3 — UI: sección en modal Ajustes (P1)
- [x] Sección "Arranque con Windows" (Activado/Desactivado) en `ui/main.html`
- [x] `openSettings` marca el estado actual; clic persiste vía API
- [x] Verificación: revisión estructural del HTML/JS (patrón igual a GPU/tema)

### T4 — README + desactivar en esta PC (P2)
- [x] README: documentar la opción nueva
- [x] Borrar `Startup\Asistemis.lnk` en esta PC (pedido del usuario) — hecho por el orquestador antes de esta tarea; esta sesión no inspeccionó ni modificó estado del sistema operativo (restricción de la tarea)
- [x] Verificación: README leído; el `.lnk` no se re-inspeccionó por restricción de la sesión

---

## Progreso

- Rama: `feature/arranque-automatico` (desde `main`)
- **PR abierto: https://github.com/AzzADesigns/Asistemis/pull/3** (desde `Shinigamy19:feature/arranque-automatico` hacia `AzzADesigns:main`)
- Estado: completado y probado en backend (ciclo activar/desactivar verificado tras fix `ad4e1fb`); UI probable por inspección + llamadas de API expuestas. La PC del usuario quedó con arranque desactivado.
- Nota de diseño: en esta rama el modal Ajustes solo tiene la fila de tema (no hay fila GPU ni `gpu_mode()` en `MainApi`, tampoco en `main`); la sección de arranque se colocó después de la fila de tema y el marcado en `openSettings` después de `markTheme`. Los selectores de tema se acotaron a `#themeRow .theme-opt` para que los botones nuevos de `#autostartRow` (misma clase `theme-opt`) no se vean afectados por `markTheme` ni por el clic de tema; el comportamiento visible del tema no cambia.
- Commits:
  - `a6cded8` feat(ajustes): opcion para activar o desactivar el arranque con Windows — herramientas.py, interfaz.py, ui/main.html
  - `48501dd` docs: documentar el ajuste de arranque con Windows en el README — README.md
  - `4329cb2` docs: registrar avance de la tarea arranque-automatico — este documento (Progreso)
  - `86d2c49` docs: registrar hashes de los work units en el documento de la tarea
  - `ad4e1fb` fix(ajustes): crear el acceso de arranque con -EncodedCommand — herramientas.py
- Prueba funcional (pedido del usuario "necesito probarlo"):
  - **Fallo encontrado**: `set_autostart(True)` devolvía False y no creaba el `.lnk`.
  - **Causa raíz**: `powershell -Command` concatena los tokens finales al texto del comando; `$args` dentro del scriptblock queda vacío y `CreateShortcut($lnk)` recibe `$null` (error: "el nombre debe terminar en .lnk"). El patrón `$args[0..4]` jamás funcionó — solo estaba verificado por sintaxis.
  - **Fix** (`ad4e1fb`): valores embebidos entre comillas simples (`'` → `''`) + `-EncodedCommand` (UTF-16LE base64). Sin `$args`, sin problemas de comillas ni de acentos.
  - **Ciclo verificado tras el fix** (venv python, vía API): `enabled_before=False` → `set_autostart(True)=True` → `.lnk` creado con Target=`Asistemis.exe` instalado, Args=`--segundo-plano`, WorkDir e Icon correctos → `set_autostart(False)=False` → `.lnk` borrado. La PC quedó en DESACTIVADO, como pidió el usuario.
  - `py_compile herramientas.py` → exit 0.
- Acción del sistema (pedido del usuario, ejecutada por el orquestador): borrado `Startup\Asistemis.lnk` en esta PC → arranque con Windows DESACTIVADO en esta máquina.
- Verificación del orquestador: `py_compile herramientas.py interfaz.py` → exit 0; `git diff main` solo en superficies permitidas; spot-check del diff conforme al diseño.
- Review RDD: assess `--base-ref main --committed-only` → riesgo **high** (`process_boundary` / `shell_process` en `herramientas.py`, 5 archivos, 165 líneas). Preflight STATUS → `fresh_target_ready`. START devolvió `gentle-ai.review-integration.consent/v3` (consentimiento del candidato). Esta sesión de runtime no expone la UI nativa `question` requerida para presentar ese sobre; según el contrato no hay fallback por chat para consent/v3 → se detuvo SIN invocar `review start --consent granted/declined`. **Review pendiente: sin autoridad ni recibo.** La entrega sigue la política ordinaria del repo.
- Espejo Engram (`odd/arranque-automatico/tasks`): **pendiente** — `mem_save` no está disponible en esta sesión; este archivo es la fuente de verdad local.
- Riesgo futuro al mergear `feature/modos-gpu`: ese trae fila GPU y selectores sin acotar en el modal; resolver el conflicto de `ui/main.html` acotando `#gpuRow` igual que `#themeRow`/`#autostartRow`.
