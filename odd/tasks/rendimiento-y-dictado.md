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
  - [ ] Prueba manual del usuario (apagar SAC → import av → instalar modelos → dictado)
  - [ ] README del dictado (pendiente tras probar)

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
