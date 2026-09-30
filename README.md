# Asistemis

Asistente de voz para Windows, **gratis y local**: toma notas, abre y cierra aplicaciones, busca en
internet y, si quieres, le pasa órdenes a [Claude](https://claude.com/claude-code). La voz se
transcribe en tu PC con [Whisper](https://github.com/SYSTRAN/faster-whisper); no se envía audio a
ningún servidor.

```
«Asistemis, anota comprar pan para mañana… eso es todo»   → se guarda en tu bloc de notas
«Asistemis, abrime Figma» / «ejecutá Steam» / «jugá al God of War»
                                                          → lo abre (o lo trae si ya estaba abierto)
«Asistemis, cerrá Chrome»                                 → cierra Chrome
«Asistemis, busca recetas de pizza»                       → abre la búsqueda en tu navegador
«Asistemis, ejecuta resumime las notas de hoy»            → se lo pregunta a Claude (opcional)
```

## Requisitos

| | |
|---|---|
| **Sistema** | Windows 11 (la interfaz usa el efecto de cristal de Windows 11; en Windows 10 no está probado) |
| **Python** | 3.10, 3.11 o 3.12 (probado con 3.12) — `winget install Python.Python.3.12` |
| **Micrófono** | cualquiera; con auriculares funciona mejor |
| **Espacio** | ~2 GB (modelos de voz) + ~1,5 GB extra si tienes tarjeta NVIDIA |
| **Tarjeta NVIDIA** | opcional pero recomendada: con ella Whisper transcribe en ~0,3 s; sin ella, en el procesador, unos segundos |
| **Claude Code** | opcional, solo para las órdenes con «ejecuta» |

## Instalación

1. Descarga el repositorio: botón **Code → Download ZIP** y descomprímelo, o
   ```
   git clone https://github.com/AzzADesigns/Asistemis.git
   ```
   Déjalo en una carpeta fija (por ejemplo `C:\Users\<tú>\Asistemis`): los accesos directos apuntan ahí.
2. Haz **doble clic en `instalar.cmd`**. El instalador:
   - crea un entorno de Python propio (`.venv`) e instala las dependencias,
   - si detecta una tarjeta NVIDIA, instala las librerías para usarla,
   - descarga los modelos de voz (~1,6 GB, solo la primera vez),
   - crea accesos directos en el menú Inicio y para que arranque con Windows,
   - y abre Asistemis.

   Opciones (desde PowerShell): `.\instalar.ps1 -SinInicioAutomatico`, `-SinDescargarModelos`,
   `-SinAccesos`, `-NoAbrir`.
3. Aparece la notificación **«Asistemis encendido»** y un icono junto al reloj. Listo.

> Si Windows muestra un aviso de seguridad al ejecutar `instalar.cmd`, es porque es un script
> descargado y sin firmar; puedes abrir `instalar.ps1` con el Bloc de notas y leer qué hace antes de
> aceptar.

### Órdenes para Claude (opcional)

1. Instala [Claude Code](https://claude.com/claude-code).
2. Abre una terminal, escribe `claude` e inicia sesión con tu cuenta.
3. Reinicia Asistemis (icono junto al reloj → *Salir*, y ábrelo desde el menú Inicio).

Sin Claude Code, todo lo demás funciona igual.

## Uso

**Ctrl+Alt+N** enciende y apaga Asistemis. Consejo: asígnalo a un botón del mouse (en Logitech,
*Logi Options+ → botón → Atajo de teclado*).

- **Encendido:** escucha siempre la palabra «Asistemis». Una burbuja con un micrófono al costado de
  la pantalla te lo recuerda (y muestra el uso de la GPU; se puede arrastrar; doble clic abre Claude).
- **Apagado:** el micrófono se cierra y los modelos salen de la tarjeta gráfica. No consume nada.

| Dices | Qué hace | Cuándo termina |
|---|---|---|
| «Asistemis, **anota** …» | Guarda la nota con fecha; la ves en la ventana de Asistemis | Al decir «eso es todo» / «eso sería todo», o tras 15 s de silencio |
| «Asistemis, **abrime** / **ejecutá** / **iniciá** / **jugá** …» | Abre la app o el juego (también los de tu biblioteca de Steam), o trae su ventana si ya está abierta | En cuanto haces una pausa |
| «Asistemis, **cerrá** Chrome» | Cierra sus ventanas, como el botón ✕ | En cuanto haces una pausa |
| «Asistemis, **busca** …» | Abre la búsqueda de Google en tu navegador | En cuanto haces una pausa |
| «Asistemis, **agregá la tarea** …» / «nueva tarea …» | La agrega como pendiente, con un número (#1, #2…), en la pestaña Tareas | Tras una pausa un poco más larga |
| «Asistemis, **estoy haciendo** / **empecé** la tarea 3» | La pasa a «En progreso» | En cuanto haces una pausa |
| «Asistemis, **terminé** / **finalicé** la tarea 3» | La pasa a «Finalizadas» | En cuanto haces una pausa |
| «Asistemis, **marcá la tarea 3 como pendiente**» / «**borrá** la tarea 3» | La vuelve a pendientes / la borra | En cuanto haces una pausa |
| «Asistemis, **ejecutá** …» (algo que no es una app) | Se lo pasa a Claude y la respuesta aparece en su panel | En cuanto haces una pausa |

- **La ventana de Asistemis** (se abre desde el menú Inicio, con doble clic en el icono junto al reloj o
  abriendo Asistemis otra vez): tus notas como un chat, agrupadas por día, con buscador; puedes escribir
  notas nuevas, copiarlas y borrarlas. Muestra también lo que abriste, cerraste y buscaste (se puede
  ocultar), tiene una pestaña con Claude y un interruptor para encender/apagar. Cerrarla no cierra
  Asistemis. Al arrancar con Windows no se abre: queda en segundo plano.
- **Tareas**: en la ventana, pestaña *Tareas*, un tablero con Pendientes / En progreso / Finalizadas.
  Puedes agregarlas escribiendo y moverlas arrastrándolas o con las flechas de cada tarjeta.
- **Ctrl+Alt+C** abre el panel de Claude, donde también puedes escribir órdenes.
- Clic derecho en el icono junto al reloj: encender/apagar, anotar sin decir «Asistemis», abrir las
  notas o salir.
- Solo abre y cierra aplicaciones del menú Inicio; nunca desinstaladores ni herramientas del sistema.

### Consumo de Claude

Anotar, abrir (o «ejecutá» una app o un juego), cerrar y buscar **no usan Claude (0 tokens)**. Solo
las órdenes con «ejecutá» que no son una app (o las escritas en el panel) consumen del límite de tu plan de Claude. Cada orden es un chat nuevo y
Claude trabaja con permisos mínimos: puede leer y buscar archivos, buscar en la web, abrir apps y
anotar, pero **no** borrar, modificar ni instalar nada (ver `claude/CLAUDE.md`).

## Personalizar

Tus datos (notas, tareas, modelos de voz, ajustes y registro) están en **`%LOCALAPPDATA%\Asistemis`**
(`Win+R` → `%LOCALAPPDATA%\Asistemis`), separados del programa.

- **Ajustes** (`ajustes.json`, se crea solo):
  - `"encendido"`: cómo arranca (recuerda el último estado).
  - `"registrar_lo_oido"`: `true` guarda en `asistemis.log` lo que oye y el audio de la última nota
    (`ultima-nota.wav`) para ajustar la activación. Por privacidad viene desactivado.
- **Nombres de apps que Whisper entiende mal**: `ALIASES`, `ALIAS_PATTERNS` y `HOTWORDS` en
  `herramientas.py`.
- **Palabra de activación y tiempos**: constantes al principio de `asistemis.py`.
- **Cómo responde Claude**: `claude/CLAUDE.md`.

## Problemas frecuentes

- **No entiende «Asistemis»**: acércate al micrófono y revisa que Windows use el micro correcto
  (Configuración → Sistema → Sonido → Entrada). Con `"registrar_lo_oido": true` verás en
  `asistemis.log` qué entiende.
- **Ctrl+Alt+N no hace nada**: puede que otro programa use ese atajo (lo dice `asistemis.log`).
- **La primera vez tarda en arrancar**: está descargando los modelos de voz, si no lo hizo el instalador.
- **Las órdenes con «ejecuta» dan error**: comprueba que `claude` funcione en una terminal y que
  hayas iniciado sesión.

## Desinstalar

Icono junto al reloj → *Salir*. Después borra:
- la carpeta del programa (la del repositorio, o `%LOCALAPPDATA%\Programs\Asistemis` si instalaste el .exe),
- tus datos (notas, tareas) y modelos de voz: `%LOCALAPPDATA%\Asistemis`,
- los accesos directos `Asistemis.lnk` del menú Inicio y de la carpeta de inicio (`Win+R` → `shell:startup`).

Tus notas y tareas están en `%LOCALAPPDATA%\Asistemis` (`notas.txt`, `tareas.json`): si quieres
conservarlas, cópialas antes de borrar esa carpeta.

## Compilar como .exe

```
powershell -ExecutionPolicy Bypass -File compilar.ps1            # crea dist\Asistemis\Asistemis.exe
powershell -ExecutionPolicy Bypass -File compilar.ps1 -Instalar  # y lo instala en %LOCALAPPDATA%\Programs\Asistemis
```

Requiere haber ejecutado antes `instalar.cmd`. El resultado es una carpeta con `Asistemis.exe` (con su
icono; en el Administrador de tareas aparece como «Asistemis») y `herramientas.exe` (la usa Claude). No
necesita Python para funcionar. Pesa ~2,3 GB con las librerías de NVIDIA; los modelos de voz se
descargan aparte la primera vez.

## Cómo está hecho

| Archivo | Qué hace |
|---|---|
| `asistemis.py` | Micrófono, detección de «Asistemis», transcripción (Whisper) y qué hacer con lo dicho |
| `herramientas.py` | Abrir/cerrar apps, buscar y guardar notas (también lo usa Claude) |
| `ordenes.py` | Conexión con Claude Code (`claude -p`) |
| `interfaz.py`, `ui/` | Interfaz en HTML/CSS (pywebview + cristal de Windows 11) |
| `claude/` | Carpeta de trabajo de Claude: instrucciones y comandos permitidos |
| `recursos/` | Icono y datos de versión del .exe |
| `asistemis.spec`, `compilar.ps1` | Receta y script para compilar el .exe (PyInstaller) |
