# Solucionar bloqueos de Windows en Asistemis

Guía completa para cuando **Windows, el antivirus o Smart App Control** bloquean partes de Asistemis (sobre todo los modelos de voz / Whisper).

---

## 1. El síntoma

En el instalador o al abrir Asistemis ves algo así:

```text
ImportError: DLL load failed while importing _core:
Una directiva de Control de aplicaciones bloqueó este archivo.
```

O en la descarga de modelos:

```text
==> Descargando los modelos de voz (~1,6 GB...)
ImportError: DLL load failed ... avformat-63-e84c...
No se pudieron descargar; se descargarán al abrir Asistemis.
```

Y/o una notificación de **Seguridad de Windows**:

> **Parte de esta aplicación se ha bloqueado**  
> Es posible que algunas características de Python no funcionen porque no podemos confirmar quién publicó `avformat-63-….dll`

**Traducción:** Python corre bien, pero Windows **no deja cargar** un `.dll` de una librería de audio/video (PyAV / ffmpeg) que Whisper necesita.

---

## 2. Por qué pasa

| Componente | Rol |
|---|---|
| **PyAV (`av`)** | Librería Python que envuelve ffmpeg; la instala `faster-whisper` |
| **DLL en `av.libs`** | Binarios de ffmpeg (`avcodec`, `avformat`, `avutil`, etc.) |
| **Smart App Control (SAC)** | Función de Windows 11 que bloquea apps/DLL **sin firma de confianza** |
| **Antivirus** | A veces también bloquea DLL “sospechosos” (códecs multimedia sin firmar) |

Los wheels de PyAV de pip **no vienen firmados** con un certificado que SAC/antivirus acepte. Por eso:

- `numpy`, `PIL`, `sounddevice`, `pywin32` → suelen cargar bien  
- `import av` / `faster_whisper` → **fallan** con “Control de aplicaciones bloqueó este archivo”

Esto **no es un bug de Asistemis ni de Python**. Es una política de seguridad de Windows (o del antivirus) que hay que relajar para esta app local.

---

## 3. Cómo identificar qué te está bloqueando

### 3.1 Mirar el registro de integridad de código

PowerShell (usuario normal alcanza para leer el log):

```powershell
Get-WinEvent -LogName "Microsoft-Windows-CodeIntegrity/Operational" -MaxEvents 20 |
  Where-Object { $_.Message -match 'av|Python|Smart App Control' } |
  Select-Object TimeCreated, Id, LevelDisplayName, Message
```

Si ves eventos tipo:

- **3118** → `Smart App Control Block Details`
- **3077 / 3033** → `Code Integrity determined that a process (python.exe) attempted to load ...`

→ el bloqueador es **Smart App Control**.

### 3.2 Estado de Smart App Control

```powershell
( Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' ).VerifiedAndReputablePolicyState
```

| Valor | Significado | ¿Se puede apagar desde Configuración? |
|------:|---|---|
| `0` | Off | ya está apagado |
| `1` | **Evaluation** | **Sí** → apagalo y listo |
| `2` | On (estricto) | En algunos builds hace falta restablecer Windows |

### 3.3 Probar el import a mano

Con el entorno de Asistemis (desde la carpeta del proyecto):

```powershell
.venv\Scripts\python.exe -c "import av; print('av OK', av.__version__)"
.venv\Scripts\python.exe -c "import faster_whisper; print('fw OK')"
```

- Si `av` falla y el resto de paquetes anda → es SAC/antivirus sobre PyAV.  
- Si ni siquiera `numpy` anda → problema de Python/entorno (otra guía: “no encuentra Python 3.10–3.12” en el README).

---

## 4. Solución recomendada (Smart App Control)

Válida cuando el registro muestra **Evaluation (`1`)**, que es lo habitual en PCs nuevas.

### Paso a paso

1. **Inicio** → escribir **Seguridad de Windows** → abrirlo.
2. **Control de apps y navegador** (App & browser control).
3. **Smart App Control**.
4. Elegir **Off** / **Desactivado**.
5. Cerrar y abrir una terminal **nueva**.
6. Verificar:

   ```powershell
   cd "C:\Users\erosd\OneDrive\Documentos\GitHub\Asistemis"
   .venv\Scripts\python.exe -c "import av; print('av OK', av.__version__)"
   ```

7. Si dice `av OK`, reejecutá el instalador o abrí Asistemis para que baje los modelos:

   ```powershell
   powershell -ExecutionPolicy Bypass -File instalar.ps1
   ```

   o doble clic en **`instalar.cmd`**.

### ¿Y si SAC está en modo estricto (`2`)?

- Apagarlo desde Configuración **puede no estar disponible**.
- En algunos builds de Windows 11, desactivar SAC estricto exige **restablecer Windows** (conserva archivos, reinstala el SO). Es desproporcionado para esta app.
- Alternativas:
  - Correr Asistemis en otra máquina / usuario sin SAC estricto.
  - Compilar/empaquetar con firma de código (fuera del alcance de esta guía).
  - Usar una distribución de Python/Whisper que el entorno confíe (no garantizado).

En la práctica: **si el valor es `1` (Evaluation), seguí la guía normal.**

---

## 5. Si el bloqueador es el antivirus (no SAC)

Algunos antivirus (Kaspersky, Bitdefender, Avast, Norton, ESET, etc.) bloquean DLL de ffmpeg por “comportamiento sospechoso”.

### Qué probar

1. **Excluir** del escaneo en tiempo real:
   - La carpeta del proyecto (donde está `.venv\`)
   - `%LOCALAPPDATA%\Asistemis\` (modelos y datos)
   - El `python.exe` del entorno:  
     `...\Asistemis\.venv\Scripts\python.exe`
2. Reintentar `import av`.
3. Mirar el historial del antivirus: suele nombrar el `.dll` bloqueado (`avcodec-…dll`, `avformat-…dll`).
4. Si el antivirus **no permite excluir**, no hay fix de usuario: hay que relajar la política o usar otra app/máquina.

> Excluir carpetas de un proyecto local de código abierto es un tradeoff aceptable. No excluir `C:\` ni el navegador.

---

## 6. Otros bloqueos “parecidos” que no son SAC

| Mensaje | Qué es | Qué hacer |
|---|---|---|
| `No encontré Python 3.10, 3.11 o 3.12` | El instalador no ve un Python compatible | `winget install Python.Python.3.12`, terminal nueva, reintentar (ver README) |
| `DLL load failed` **sin** “Control de aplicaciones” | Falta runtime C++ o dependencia | Instalar *Microsoft Visual C++ Redistributable* 2015–2022 (x64) |
| `Could not find module … (or one of its dependencies)` | DLL de `av.libs` no está en el search path | Usar el `.venv` del proyecto (el wheel ya lo resuelve); no copies solo un `.pyd` |
| Modelos tardan / fallan la 1.ª vez | Descarga de ~1,6 GB | Dejar terminar; si se corta, abrir Asistemis y reintenta |
| `claude` no encontrado | Claude Code opcional no instalado | Todo lo demás funciona; instalar Claude solo si usás “ejecutá” |

---

## 7. Flujo de decisión (resumen)

```text
¿Falla import av / faster_whisper / modelos?
        │
        ├─ ¿Eventos "Smart App Control Block" o notificación de Seguridad de Windows?
        │         │
        │         ├─ SAC = 1 (Evaluation) → Apagar SAC → reintentar av → instalar
        │         └─ SAC = 2 (On) → ver sección 4 (puede requerir restablecer)
        │
        ├─ ¿Antivirus con alerta sobre avcodec/avformat?
        │         → Excluir carpeta del proyecto + %LOCALAPPDATA%\Asistemis → reintentar
        │
        ├─ ¿Error de Python / instalador?
        │         → Guía Python 3.10–3.12 (README)
        │
        └─ ¿Falta runtime C++?
                  → VC++ Redistributable x64 → reintentar
```

---

## 8. Verificación final (todo OK)

```powershell
cd "C:\Users\erosd\OneDrive\Documentos\GitHub\Asistemis"
.venv\Scripts\python.exe -c "import av, faster_whisper; print('OK', av.__version__)"
```

Y en la app:

1. Ctrl+Alt+N → «Asistemis encendido»
2. «Asistemis, anota hola… eso es todo» → nota visible
3. «Asistemis, mododictado» → ventana de dictado → «eso es todo» → nota `[dictado]`
4. Apagar → en el Task Manager baja la RAM (modelos descargados)

---

## 9. Notas para desarrolladores del repo

- El instalador (`instalar.ps1`) ahora:
  - Detecta Python 3.10–3.12 con splatting **array** (`@($args)`) y fallback a  
    `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
  - Lee `VerifiedAndReputablePolicyState` y avisa si SAC está activo.
  - Prueba `import av` después de pip y imprime instrucciones si falla.
- Los eventos útiles en el log:
  - `Microsoft-Windows-CodeIntegrity/Operational` (Smart App Control / Code Integrity)
- Los binarios críticos viven en:  
  `.venv\Lib\site-packages\av\` y `.venv\Lib\site-packages\av.libs\`
- **No** “arreglar” el bloqueo copiando DLL a mano ni deshabilitando el firewall global: apagar SAC (Evaluation) o excluir la carpeta del proyecto es el camino soportado.

---

## 10. Preguntas frecuentes

**¿Puedo dejar SAC apagado?**  
Sí, es un tradeoff de seguridad personal. Asistemis es 100 % local y no sube tu voz a la nube; el riesgo es el de correr cualquier app de terceros sin filtro de firma.

**¿Por qué solo falla Python y no el navegador?**  
SAC/antivirus miran la **carga de DLL en procesos**. El navegador no carga `avformat-63-….dll` de un venv de Python.

**¿Se arregla solo si espero?**  
En modo Evaluation, SAC puede “aprender” reputación con el tiempo, pero wheels de ffmpeg de pip suelen seguir bloqueados. Apagar SAC (o excluir el antivirus) es lo fiable.

**¿Formato del error en inglés?**  
Windows en inglés: `DLL load failed while importing _core: This application is blocked by system policy` / similar. La guía aplica igual.

---

<div align="center">
<sub>Asistemis · Troubleshooting Windows (Smart App Control / antivirus / Python)</sub>
</div>
