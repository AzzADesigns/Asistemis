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
- Estado: completado (verificación de sintaxis + estructural; sin ejecutar la app ni crear/borrar accesos de Inicio en esta sesión)
- Nota de diseño: en esta rama el modal Ajustes solo tiene la fila de tema (no hay fila GPU ni `gpu_mode()` en `MainApi`, tampoco en `main`); la sección de arranque se colocó después de la fila de tema y el marcado en `openSettings` después de `markTheme`. Los selectores de tema se acotaron a `#themeRow .theme-opt` para que los botones nuevos de `#autostartRow` (misma clase `theme-opt`) no se vean afectados por `markTheme` ni por el clic de tema; el comportamiento visible del tema no cambia.
- Commits:
  - `a6cded8` feat(ajustes): opcion para activar o desactivar el arranque con Windows — herramientas.py, interfaz.py, ui/main.html
  - `48501dd` docs: documentar el ajuste de arranque con Windows en el README — README.md
  - `docs: registrar avance de la tarea arranque-automatico` — este documento (Progreso)
