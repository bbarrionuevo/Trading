# Instala (o actualiza) la tarea programada "trading-agente": el agente de TRADING 24/7 en esta PC.
#   - Arranca con Windows y al iniciar sesion; si se cae, Windows lo reinicia al minuto (sin limite).
#   - Corre sin pedir contrasena (S4U) y con permisos normales (no administrador).
#   - Usa SOLO las carpetas de TRADING. Nunca toca tareas ni carpetas de nexus (REGLAS G-5).
# Uso (PowerShell como administrador, una vez):
#   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\TRADING\deploy\windows\instalar-agente.ps1"
param(
  [string]$Repo = "$env:USERPROFILE\TRADING"
)
$ErrorActionPreference = "Stop"
$TaskName = "trading-agente"
if ($Repo -match '(?i)nexus') { throw "TRADING nunca usa carpetas de nexus: $Repo" }
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  throw "No encuentro $Python. Primero instala el entorno (docs/OPERACION.md, 'Instalacion')."
}
& $Python -m tradingagent --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "El agente no arranca con $Python" }

$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m tradingagent run" -WorkingDirectory $Repo
$Triggers = @(
  (New-ScheduledTaskTrigger -AtStartup),
  (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")
)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -RestartCount 9999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers -Settings $Settings `
  -Principal $Principal -Description "TRADING: agente de noticias en paper, 24/7 (no es nexus)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Listo: la tarea '$TaskName' quedo instalada y arrancando. Te llega por Telegram 'Agente iniciado'."
Write-Host "Ver estado:  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "Logs:        $env:LOCALAPPDATA\TRADING\logs\agent.log"
Write-Host "Pausa:       crear el archivo vacio $env:LOCALAPPDATA\TRADING\KILL (salir siempre se puede)"
