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
Paso 'Buscando Python 3.12'
$pyExe = $null
foreach ($candidato in @('py -3.12', 'py -3.11', 'py -3.10', 'python')) {
    $exe, $extra = $candidato -split ' '
    try {
        $version = & $exe @extra -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])' 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -in @('310', '311', '312')) { $pyExe, $pyExtra = $exe, $extra; break }
    } catch {}
}
if (-not $pyExe) {
    Fallo "No encontré Python 3.10, 3.11 o 3.12. Instálalo con:`n    winget install Python.Python.3.12`ny vuelve a ejecutar el instalador."
}
Write-Host ("Python {0}.{1}" -f [math]::Floor([int]$version / 100), ([int]$version % 100))

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

# 3. Modelos de voz (Whisper)
if (-not $SinDescargarModelos) {
    Paso 'Descargando los modelos de voz (~1,6 GB, solo la primera vez)'
    $modelos = if ($gpu) { "'large-v3-turbo'" } else { "'large-v3-turbo', 'base'" }
    $cache = (Join-Path $env:LOCALAPPDATA 'Asistemis\models\whisper').Replace('\', '/')
    & $py -c "from faster_whisper import download_model; [download_model(m, cache_dir='$cache') for m in ($modelos,)]"
    if ($LASTEXITCODE -ne 0) { Write-Host 'No se pudieron descargar; se descargarán al abrir Asistemis.' -ForegroundColor Yellow }
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
