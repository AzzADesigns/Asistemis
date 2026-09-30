# Instalador de Asistemis: entorno de Python, dependencias, modelos de voz y accesos directos.
# Uso: doble clic en instalar.cmd  (o: powershell -ExecutionPolicy Bypass -File instalar.ps1)
param(
    [switch]$SinInicioAutomatico,   # no arrancar Asistemis con Windows
    [switch]$SinDescargarModelos,   # los modelos se descargarán al abrirlo la primera vez
    [switch]$SinAccesos,            # no crear accesos directos (instalación portátil)
    [switch]$NoAbrir                # no abrir Asistemis al terminar
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

function Paso($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }
function Fallo($texto) { Write-Host "`n$texto" -ForegroundColor Red; exit 1 }

# 1. Python 3.10 - 3.12
# Nota: la detección usa @($args) y no @args. En PowerShell 5.1, splatting un string
# enumera caracteres ("py -3.12" llegaba roto al lanzador) y el instalador fallaba
# aunque Python 3.12 estuviera bien instalado. Con array se pasa un solo argumento.
Paso 'Buscando Python 3.12'
$pyExe = $null
$pyExtra = @()
$versionTxt = ''
$candidatos = @(
    , @('py', @('-3.12')),
    , @('py', @('-3.11')),
    , @('py', @('-3.10')),
    , @('python', @())
)
# Red de seguridad: instalación típica de winget si el lanzador py no la registra
$py312Directo = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (Test-Path $py312Directo) { $candidatos += , @($py312Directo, @()) }

foreach ($par in $candidatos) {
    $exe = $par[0]
    $extra = @($par[1])
    try {
        $ErrorActionPreference = 'Continue'
        $raw = & $exe @extra -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])' 2>$null
        $code = $LASTEXITCODE
    } catch {
        $raw = $null
        $code = 1
    } finally {
        $ErrorActionPreference = 'Stop'
    }
    $versionTxt = ("$raw").Trim() -split "`r?`n" | Select-Object -First 1
    if ($code -eq 0 -and $versionTxt -in @('310', '311', '312')) {
        $pyExe = $exe
        $pyExtra = $extra
        break
    }
}
if (-not $pyExe) {
    Fallo "No encontré Python 3.10, 3.11 o 3.12. Instálalo con:`n    winget install Python.Python.3.12`ny vuelve a ejecutar el instalador."
}
$pyVer = [int]$versionTxt
Write-Host ("Python {0}.{1}" -f [math]::Floor($pyVer / 100), ($pyVer % 100))

# 2. Entorno virtual y dependencias
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Paso 'Creando el entorno (.venv)'
    & $pyExe @pyExtra -m venv .venv
}
$py = Join-Path $root '.venv\Scripts\python.exe'
Paso 'Instalando dependencias'
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fallo 'Falló la instalación de dependencias.' }

$gpu = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($gpu) {
    Paso 'Tarjeta NVIDIA detectada: instalando librerías CUDA (~1,5 GB)'
    & $py -m pip install -r requirements-gpu.txt
    if ($LASTEXITCODE -ne 0) { Write-Host 'No se pudieron instalar; Asistemis usará el procesador (más lento).' -ForegroundColor Yellow }
} else {
    Write-Host 'Sin tarjeta NVIDIA: Whisper usará el procesador (las notas tardan unos segundos más).' -ForegroundColor Yellow
}

# Smart App Control (Windows 11) puede bloquear los DLL de PyAV/ffmpeg:
# "DLL load failed ... Control de aplicaciones bloqueó este archivo"
$sac = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
# 0=Off 1=Evaluation 2=On
if ($sac -in 1, 2) {
    Write-Host "`nSmart App Control está activo (modo $sac) y puede bloquear los modelos de voz." -ForegroundColor Yellow
    Write-Host 'Si falla la descarga o al abrir: Seguridad de Windows → Control de apps y navegador' -ForegroundColor Yellow
    Write-Host '→ Smart App Control → Off. Luego volvé a ejecutar instalar.cmd.' -ForegroundColor Yellow
}

Paso 'Comprobando PyAV ( Whisper lo necesita)'
$ErrorActionPreference = 'Continue'
& $py -c 'import av; print("av", av.__version__)' 2>$null
$avCode = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($avCode -ne 0) {
    Write-Host 'PyAV no carga. Casi siempre es Smart App Control bloqueando los DLL de ffmpeg.' -ForegroundColor Red
    Write-Host 'Apagalo (ver mensaje anterior) y volvé a ejecutar instalar.cmd.' -ForegroundColor Red
    Write-Host 'La instalación continúa, pero las notas/dictado no van a funcionar hasta resolverlo.' -ForegroundColor Yellow
}

# 3. Modelos de voz (Whisper)
if (-not $SinDescargarModelos) {
    Paso 'Descargando los modelos de voz (~1,6 GB, solo la primera vez)'
    $modelos = if ($gpu) { "'large-v3-turbo'" } else { "'large-v3-turbo', 'base'" }
    $cache = (Join-Path $env:LOCALAPPDATA 'Asistemis\models\whisper').Replace('\', '/')
    & $py -c "from faster_whisper import download_model; [download_model(m, cache_dir='$cache') for m in ($modelos,)]"
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'No se pudieron descargar; se descargarán al abrir Asistemis.' -ForegroundColor Yellow
        Write-Host 'Si el error menciona "Control de aplicaciones", apagá Smart App Control y reintentá.' -ForegroundColor Yellow
    }
}

# 4. Accesos directos (menú Inicio y, si se quiere, arranque con Windows)
$destinos = @()
if (-not $SinAccesos) {
    Paso 'Creando accesos directos'
    $destinos += [Environment]::GetFolderPath('Programs')
    if (-not $SinInicioAutomatico) { $destinos += [Environment]::GetFolderPath('Startup') }
}
$shell = New-Object -ComObject WScript.Shell
foreach ($carpeta in $destinos) {
    $acceso = $shell.CreateShortcut((Join-Path $carpeta 'Asistemis.lnk'))
    $acceso.TargetPath = Join-Path $root '.venv\Scripts\pythonw.exe'
    $acceso.Arguments = '"' + (Join-Path $root 'asistemis.py') + '"'
    $acceso.WorkingDirectory = $root
    $acceso.Description = 'Asistemis: notas y órdenes por voz'
    if ($carpeta -eq [Environment]::GetFolderPath('Startup')) { $acceso.Arguments = ($acceso.Arguments + ' --segundo-plano').Trim() }
    $acceso.Save()
    Write-Host "  $carpeta\Asistemis.lnk"
}

# 5. Claude Code (opcional: solo para las órdenes "Asistemis, ejecuta...")
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "`nClaude Code no está instalado. Asistemis funciona igual (notas, abrir, cerrar, buscar);" -ForegroundColor Yellow
    Write-Host 'para las órdenes con "ejecuta" instálalo desde https://claude.com/claude-code e inicia sesión con: claude' -ForegroundColor Yellow
}

if ($NoAbrir) { Paso 'Listo.'; exit 0 }
Paso 'Listo. Abriendo Asistemis (icono junto al reloj; Ctrl+Alt+N lo enciende y apaga)'
Start-Process -FilePath (Join-Path $root '.venv\Scripts\pythonw.exe') -ArgumentList ('"' + (Join-Path $root 'asistemis.py') + '"') -WorkingDirectory $root
