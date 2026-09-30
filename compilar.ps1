# Compila Asistemis como .exe (dist\Asistemis) y, con -Instalar, lo instala para este usuario.
# Uso: powershell -ExecutionPolicy Bypass -File compilar.ps1 [-Instalar] [-SinInicioAutomatico]
param(
    [switch]$Instalar,              # copiar a %LOCALAPPDATA%\Programs\Asistemis y crear accesos directos
    [switch]$SinInicioAutomatico    # con -Instalar: no arrancar con Windows
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host 'Primero ejecuta instalar.cmd (crea el entorno de Python).' -ForegroundColor Red; exit 1 }

# si el .exe de dist\ está abierto, sus archivos están bloqueados y la compilación falla
Get-Process Asistemis -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$root\dist\*" } | Stop-Process -Force

Write-Host "`n==> Compilando (1-2 minutos)" -ForegroundColor Cyan
& $py -m pip install --quiet pyinstaller
& $py -m PyInstaller asistemis.spec --noconfirm --log-level WARN
if ($LASTEXITCODE -ne 0) { Write-Host 'Falló la compilación.' -ForegroundColor Red; exit 1 }
Write-Host "Listo: $root\dist\Asistemis\Asistemis.exe"
if (-not $Instalar) { exit 0 }

$destino = Join-Path $env:LOCALAPPDATA 'Programs\Asistemis'
Write-Host "`n==> Instalando en $destino" -ForegroundColor Cyan
Get-Process Asistemis -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*asistemis.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 1
robocopy (Join-Path $root 'dist\Asistemis') $destino /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Host 'Falló la copia.' -ForegroundColor Red; exit 1 }

$shell = New-Object -ComObject WScript.Shell
$carpetas = @([Environment]::GetFolderPath('Programs'))
if (-not $SinInicioAutomatico) { $carpetas += [Environment]::GetFolderPath('Startup') }
foreach ($carpeta in $carpetas) {
    $acceso = $shell.CreateShortcut((Join-Path $carpeta 'Asistemis.lnk'))
    $acceso.TargetPath = Join-Path $destino 'Asistemis.exe'
    $acceso.Arguments = ''
    $acceso.WorkingDirectory = $destino
    $acceso.IconLocation = (Join-Path $destino 'Asistemis.exe') + ',0'
    $acceso.Description = 'Asistemis: notas y órdenes por voz'
    if ($carpeta -eq [Environment]::GetFolderPath('Startup')) { $acceso.Arguments = ($acceso.Arguments + ' --segundo-plano').Trim() }
    $acceso.Save()
}
Start-Process (Join-Path $destino 'Asistemis.exe')
Write-Host "`n==> Instalado y abierto." -ForegroundColor Cyan
