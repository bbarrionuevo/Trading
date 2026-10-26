<#
  Instala nexus en Windows 11, en MODO PAPER (no existe código para órdenes reales).
  Uso (PowerShell, sin privilegios de administrador):
    powershell -ExecutionPolicy Bypass -File install.ps1
  Opciones:
    -Dir <ruta>     carpeta del despliegue (por defecto %USERPROFILE%\nexus). Es un clon aparte del
                    repo de desarrollo: solo sigue la rama main validada por el CI.
    -KeepSleep      no tocar la configuración de suspensión.
  Qué hace: instala Git/Python si faltan (winget), clona main, crea el entorno virtual, corre los
  tests (si fallan, no instala), crea %LOCALAPPDATA%\nexus (estado, logs, .env privado) y registra
  dos tareas programadas: "nexus-agente" (al iniciar sesión, se reinicia sola) y
  "nexus-mantenimiento" (cada 30 min: actualización segura + latido a GitHub). El mantenimiento crea
  además "nexus-dashboard" (tablero local de solo lectura en http://localhost:8765).
#>
param(
  [string]$Dir = "$env:USERPROFILE\nexus",
  [string]$Repo = "https://github.com/bbarrionuevo/nexus.git",
  [switch]$KeepSleep
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"   # Python lee y escribe UTF-8 (evita errores de cp1252 en Windows)

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User")
}
function Need($cmd, $id) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "Instalando $id con winget..."
    winget install -e --id $id --accept-source-agreements --accept-package-agreements
    Refresh-Path
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "No se encontró '$cmd' tras instalar $id" }
  }
}

Need git "Git.Git"
Need py "Python.Python.3.12"

if (-not (Test-Path "$Dir\.git")) { git clone --branch main $Repo $Dir }
git -C $Dir fetch -q origin main
git -C $Dir checkout -q --detach origin/main

$NexusHome = "$env:LOCALAPPDATA\nexus"
New-Item -ItemType Directory -Force "$NexusHome\state", "$NexusHome\logs" | Out-Null
$EnvFile = "$NexusHome\.env"
if (-not (Test-Path $EnvFile)) {
  "# Claves de MEXC (solo lectura por ahora). Este archivo NUNCA va al repositorio.`r`nMEXC_API_KEY=`r`nMEXC_SECRET_KEY=" |
    Set-Content -Encoding ascii $EnvFile
}
icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

$Py = "$Dir\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { py -3 -m venv "$Dir\.venv" }
& $Py -c "import sys; assert sys.version_info >= (3, 10), sys.version"
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q -r "$Dir\local\requirements.txt"

Push-Location "$Dir\local"
& $Py -m pytest -q -p no:cacheprovider
$TestsOk = ($LASTEXITCODE -eq 0)
Pop-Location
if (-not $TestsOk) { throw "Los tests fallaron en esta PC: no se registran las tareas." }

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew
$Ps = "powershell.exe"
$Common = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File"

# Reinstalación: detener el agente anterior (tarea y procesos huérfanos) antes de registrar de nuevo.
if (Get-ScheduledTask -TaskName "nexus-agente" -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName "nexus-agente" -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*nexus.agent.maker_agent*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$A1 = New-ScheduledTaskAction -Execute $Ps -Argument "$Common `"$Dir\local\windows\run_agent.ps1`""
$T1 = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# Autorreparación: cada minuto Windows intenta lanzarla; si ya corre, lo ignora (IgnoreNew).
$T1b = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "nexus-agente" -Action $A1 -Trigger @($T1, $T1b) -Settings $Settings -Force | Out-Null

$A2 = New-ScheduledTaskAction -Execute $Ps -Argument "$Common `"$Dir\local\windows\maintenance.ps1`""
$T2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "nexus-mantenimiento" -Action $A2 -Trigger $T2 -Settings $Settings -Force | Out-Null

if (-not $KeepSleep) {
  try {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    Write-Host "Suspensión con corriente desactivada (revertir: powercfg /change standby-timeout-ac 30)."
  } catch { Write-Warning "No se pudo cambiar la suspensión: desactivala a mano en Configuración > Energía." }
}

Start-ScheduledTask -TaskName "nexus-agente"
Write-Host ""
Write-Host "Instalado en $Dir (modo paper). Datos y logs en $NexusHome"
Write-Host "Tablero:     http://localhost:8765   (disponible en unos minutos; acceso directo en el escritorio)"
Write-Host "Estado:      Get-Content $NexusHome\state\status.json"
Write-Host "Parar todo:  New-Item $NexusHome\KILL   (el agente deja de abrir posiciones)"
Write-Host "Claves:      notepad $EnvFile   y luego:  $Py -m nexus.check_keys  (desde $Dir\local)"
