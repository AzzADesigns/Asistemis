# Feature: Rendimiento + Dictado Continuo

## Objetivo
Reducir consumo innecesario de CPU/RAM en Asistemis y añadir un modo de transcripción continua por voz (dictado largo con ventana en vivo que al final se guarda como nota).

## Problema / Por qué
- El probe en RECORDING re-transcribe todo el audio en cada pausa (coste O(n²)).
- Whisper no libera RAM al apagar.
- Claude arranca aunque no se use.
- No existe forma de dictar un texto largo sin wake word ni que se interprete como comando.

## Alcance
- Optimizar probe (incremental), unload de RAM, warm-up perezoso de Claude, cálculo de nivel RMS.
- Nuevo modo DICTATING en Engine con rolling windows, ventana UI en vivo y cierre por voz o botón.
- Guardar al final como nota con tag `[dictado]`.

## Fuera de alcance
- Cambiar la UX del wake word o los modelos por defecto.
- Streaming a Claude durante el dictado.
- Soporte multi-micrófono o idiomas adicionales.

## Restricciones
- No romper: parse(), by_button/"Anotar ahora", generation, probe→_run_text, notas.txt, CLI de herramientas, PyInstaller.
- Dictado NUNCA debe pasar por parse() como comando ni por _probe.
- Respetar whisper_ready.wait() antes de usar whisper.
- Texto de UI en español (proyecto es de habla hispana); nombres de código en inglés.

## Modo TDD
- Fuente: no hay tests en el repo (solo manual). Se verifica manualmente + lint básico.
- Runner: N/A — verificación funcional manual y revisión de flujo.

## Entrega
- Estrategia: ask-on-risk por defecto (si forecast > 400 líneas, preguntar).
- Candidato: work-unit commits en rama feature.

---

## Tareas

### T1 — Probe incremental (P1, alto)
- [ ] En `asistemis.py`, cambiar `_probe` para que no re-transcriba el audio completo en cada pausa.
- [ ] Mantener resultado por generación; no duplicar trabajo si el texto parcial ya es suficiente.
- [ ] Verificar: flujo "anota … eso es todo" sigue funcionando; órdenes con pausa se ejecutan.
- Ruta: delegated (asistemis.py + posible touch en herramientas si hace falta)
- Evidencia: análisis + prueba manual del flujo de notas/órdenes.

### T2 — Liberar RAM al apagar (P3, medio)
- [ ] En `_maybe_unload` / apagado: descargar pesos de CPU (no solo mover a CPU).
- [ ] Evitar recarga costosa al reencender: mantener listener base para wake, recargar turbo solo cuando haga falta.
- Ruta: delegated
- Evidencia: memoria antes/después en Task Manager al apagar.

### T3 — Claude perezoso (P13, medio)
- [ ] No arrancar `claude -p` en el warm-up si el usuario no lo usó todavía.
- [ ] Lazy-init en el primer "ejecutá" o al abrir el panel.
- Ruta: delegated
- Evidencia: proceso claude no aparece al iniciar Asistemis sin usarlo.

### T4 — Micro-optimizaciones de nivel (P5, bajo)
- [ ] Calcular `level(block)` una sola vez por bloque en RECORDING.
- [ ] Cache simple de niveles recientes para `_maybe_check`.
- Ruta: inline (mecánico)
- Evidencia: diff + lectura.

### T5 — Estado DICTATING en Engine
- [ ] Nuevo flag/estado de dictado en `Engine` (sin wake, sin probe, sin parse).
- [ ] Rolling window de audio (ventanas de 5-10 s) en lugar de chunks ilimitados.
- [ ] Transcripción parcial en hilos aparte respetando `generation` y `whisper_ready`.
- [ ] Comandos: `start_dictation`, `stop_dictation` (voz + UI + tray).
- [ ] Cierre por: frase ("eso es todo", "detené", "listo"), botón, tray, o Ctrl+Alt+N (decidir: guardar o descartar parcial → guardar por defecto).
- [ ] Al final: `save_note(texto, tag="[dictado]")`.
- Ruta: delegated (asistemis.py)
- Evidencia: flujo completo dictado.

### T6 — Ventana de dictado en vivo
- [ ] Nueva ventana Glass `dictate` (patrón existente en interfaz.py).
- [ ] Recibe `dictation_delta` / parciales y los muestra en crecimiento.
- [ ] Botones: Listar/Detener (salvar) y Cancelar (descartar).
- [ ] Integración en Interface._handle/_dictation.
- Ruta: delegated (interfaz.py + ui/dictate.html + ui/*.js si aplica)
- Evidencia: ventana visible y actualizándose.

### T7 — Comando de voz para iniciar dictado
- [ ] Parsear frases tipo "Asistemis, transcribí", "modo dictado", "empezá a transcribir".
- [ ] No confundir con "anota" (modo actual debe seguir intacto).
- [ ] Al dictar, las órdenes normales quedan suspendidas hasta detener.
- Ruta: delegated (asistemis.py parse + Engine)
- Evidencia: comando de voz inicia el modo.

### T8 — Integración y verificación final
- [ ] "Anotar ahora" (by_button) no se rompe.
- [ ] Notas, tareas, apps, música, Claude siguen igual sin dictado.
- [ ] Documentar en README el nuevo comando.
- [ ] Forecast de líneas + decisión de PR si excede 400.
- Ruta: delegated
- Evidencia: checklist completo.

---

## Progreso
- [x] T1 — probe incremental (commit 6351cd2)
- [x] T2 — RAM unload al apagar (commit 6351cd2)
- [x] T3 — Claude perezoso (commit 6351cd2)
- [x] T4 — RMS una sola vez (commit 6351cd2)
- [x] T5 — estado DICTATING en Engine (commit 53f222c)
- [x] T6 — ventana de dictado en vivo (commit 53f222c)
- [x] T7 — comando de voz para iniciar dictado (commit 53f222c)
- [ ] T8 — integración y verificación final
  - [x] Fix detección de Python en instalar.ps1 (splatting PS 5.1 + fallback a ruta Python312)
  - [x] Detección SAC + chequeo `import av` en instalar.ps1
  - [x] docs/bloqueos-windows.md — guía completa Smart App Control / antivirus / PyAV
  - [x] README enlaza la guía y documenta el error de Control de aplicaciones
  - [x] Acceso directo en Escritorio (instalar.ps1 + creado en esta máquina)
- [x] Instalación completa en la PC del usuario (modelos GPU, Asistemis corriendo)
- [x] Acceso directo en Escritorio
- [x] .exe compilado e instalado en %LOCALAPPDATA%\Programs\Asistemis (sin commit: usuario pidió cambios locales)
  - Desktop → Asistemis.exe corriendo, whisper en GPU
  - instalar.ps1 ahora compila el .exe por defecto (-NoCompilar para saltarlo)
  - compilar.ps1 crea acceso en Escritorio
- [x] Ayuda por voz + pestaña Ayuda en la UI + botón en bandeja (sin commit)
  - «¿cómo funciona?» / «¿qué comandos hay?» / «¿cómo detengo la transcripción?»
  - Preguntas sobre Asistemis → respuesta local; otras → Claude
  - dictate.html muestra cómo cortar; tip al iniciar dictado
  - Tests parse: 9/9 OK
  - .exe regenerado con estos cambios
- [x] Botón Ajustes al lado de Encendido: tema Oscuro / Claro / Sistema (sin commit)
  - Persistido en ajustes.json ("theme")
  - base.css data-theme=light; page() inyecta applyTheme en todas las ventanas
  - MainApi.theme / set_theme; herramientas.get_theme/set_theme
  - Layout: engranaje ARIBA de Encendido; power height 46px fijo
  - .exe regenerado
- [x] Optimización de flujo de apps (sin commit)
  - Índice precomputado (key + words) al cargar la lista
  - Match exacto O(1), prefijo, fuzzy con poda real_quick_ratio/quick_ratio
  - Caché de resultados find_app (45 s) y de juegos Steam (mtime de vdf)
  - MRU: bonus a apps abiertas hace poco
  - app_windows: match barato (título/exe) primero; COM AppUserModelID solo si falta
  - Apertura: ShellExecuteW para AUMID; os.startfile para .lnk/.exe/steam://
  - Fallback: atajos del menú Inicio + rutas comunes (Chrome, Firefox, VS Code, Discord, Spotify)
  - Medido: 207 apps, find_app ~0–4 ms (antes fuzzy full list)
  - Chrome no está instalado en esta PC (solo Brave remote) — find_app None es correcto
  - Modal Ajustes: fondo opaco (#1c1e2a / #f7f8fc), sin bleed-through
  - «abrime el navegador» → navegador predeterminado del sistema (ProgId http + fallbacks)
  - Quitado alias "navegador"→Chrome; verbos abrime/inicia se limpian en find_app
  - .exe regenerado
- [x] Pausa/detener música por voz (sin commit → ahora commit detallado)
  - «detene la música» / «pausá» / «stop música» / «pará el youtube»
  - pause_music(): UIA sobre barra del reproductor + fallback tecla multimedia
  - music_stop antes de help en parse (no robar "detene el dictado" al dictado real)
  - Toastes en interfaz.py; ayuda de música actualizada
  - Quitado open_app duplicado que había quedado en herramientas.py
- [ ] Prueba manual del usuario (nota / dictado / ayuda / tema / apps / música)
- [ ] README del dictado + comandos de ayuda + ajustes de tema + pausa música
- [ ] Push de la rama (cuando el usuario confirme que funciona)

## Prueba manual sugerida (antes del push)
1. Nota larga con pausas: "Asistemis, anota … eso es todo" → se guarda completa (probe incremental).
2. Apagar (Ctrl+Alt+N) → RAM en Task Manager baja (modelos descargados).
3. Reencender → wake vuelve (recarga modelos).
4. "Asistemis, ejecutá resumime algo" sin haber usado Claude → Claude arranca en ese momento.
5. Dictado: "Asistemis, mododictado" o "transcribí" → ventana en vivo; hablar un rato; "eso es todo" o botón Listo → nota [dictado] en el panel.
6. "Anotar ahora" del tray sigue funcionando igual (no es dictado).
7. Abrir apps / tareas / música sin cambios.

## Evidencia / Notas
- Análisis de arquitectura completado (explore): problemas P1-P14 identificados con líneas.
- Decisión de producto: salida = ventana en vivo + nota `[dictado]`.
- Commit por work unit al terminar cada tarea.
- T5-T7: dictado continuo con flag `dictating` sobre RECORDING; rolling window ~10 s;
  parciales incrementales sin parse; frases de fin fuzzy sin wake word; UI Glass `dictate`
  con botones Listo/Cancelar; parse detecta "transcribí / modo dictado / iniciá la transcripción"
  solo al inicio de la frase (no roba "buscá transcripción" ni "iniciá Chrome").
- py_compile OK en asistemis.py e interfaz.py; smoke test de parse: 0 fallas.
