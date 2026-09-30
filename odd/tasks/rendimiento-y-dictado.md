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
- [ ] T1
- [ ] T2
- [ ] T3
- [ ] T4
- [ ] T5
- [ ] T6
- [ ] T7
- [ ] T8

## Evidencia / Notas
- Análisis de arquitectura completado (explore): problemas P1-P14 identificados con líneas.
- Decisión de producto: salida = ventana en vivo + nota `[dictado]`.
- Commit por work unit al terminar cada tarea.
