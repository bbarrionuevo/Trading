# Instala (o actualiza) la tarea programada "trading-agente": el supervisor de TRADING 24/7 en esta PC.
#   - Corre "python -m tradingagent supervisar": mantiene al agente vivo (lo relanza al instante si se
#     cae), lo actualiza solo cuando main tiene una version nueva con CI verde (y vuelve atras si no
#     arranca) y sirve el tablero en http://127.0.0.1:8765 (REGLAS G-7).
#   - Arranca con Windows, al iniciar sesion y, por las dudas, cada minuto: si ya corre no se duplica.
#     Asi vuelve solo si el supervisor se actualiza a si mismo (sale con codigo 75) o si algo lo corta.
#   - Corre sin pedir contrasena (S4U) y con permisos normales (no administrador).
#   - Usa SOLO las carpetas de TRADING. Nunca toca tareas ni carpetas de nexus (REGLAS G-5).
# Uso (PowerShell como administrador; instalar.ps1 lo llama solo):
#   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\TRADING\deploy\windows\instalar-agente.ps1"
param(
  [string]$Repo = "$env:USERPROFILE\TRADING",
  [switch]$SinIniciar                    # solo registrar (lo usa la prueba en Windows del CI)
)
$ErrorActionPreference = "Stop"
$TaskName = "trading-agente"
if ($Repo -match '(?i)nexus') { throw "TRADING nunca usa carpetas de nexus: $Repo" }
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  throw "No encuentro $Python. Primero corre deploy\windows\instalar.ps1 (docs/OPERACION.md)."
}
& $Python -m tradingagent --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "El agente no arranca con $Python" }

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue      # cierra la version anterior
  Start-Sleep -Seconds 3
}

$Usuario = "$env:USERDOMAIN\$env:USERNAME"
function Disparadores([bool]$CadaMinuto) {
  $t = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $Usuario))
  if ($CadaMinuto) {
    $inicio = (Get-Date).AddMinutes(1)
    try {
      $t += New-ScheduledTaskTrigger -Once -At $inicio -RepetitionInterval (New-TimeSpan -Minutes 1)
    } catch {
      $t += New-ScheduledTaskTrigger -Once -At $inicio -RepetitionInterval (New-TimeSpan -Minutes 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    }
  }
  return $t
}
$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m tradingagent supervisar" -WorkingDirectory $Repo
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -RestartCount 9999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $Usuario -LogonType S4U -RunLevel Limited
$Descripcion = "TRADING: supervisor del agente de noticias en paper, 24/7, se actualiza solo (no es nexus)"
$CadaMinuto = $true
try {
  Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger (Disparadores $true) -Settings $Settings `
    -Principal $Principal -Description $Descripcion -Force | Out-Null
} catch {
  Write-Host "Aviso: sin el disparador de cada minuto ($($_.Exception.Message))." -ForegroundColor Yellow
  $CadaMinuto = $false
  Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger (Disparadores $false) -Settings $Settings `
    -Principal $Principal -Description $Descripcion -Force | Out-Null
}
# Marca para el supervisor: solo se recarga a si mismo si la tarea lo vuelve a lanzar en un minuto.
$Estado = Join-Path $env:LOCALAPPDATA "TRADING\state"
New-Item -ItemType Directory -Force -Path $Estado | Out-Null
$Marca = @{ cada_minuto = $CadaMinuto; instalada = (Get-Date).ToUniversalTime().ToString("o") } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $Estado "tarea.json"), $Marca, (New-Object Text.UTF8Encoding($false)))
if ($SinIniciar) { Write-Host "Tarea '$TaskName' registrada (sin iniciar)."; exit 0 }
Start-ScheduledTask -TaskName $TaskName
Write-Host "Listo: la tarea '$TaskName' quedo instalada y arrancando. Te llega por Telegram 'Agente iniciado'."
Write-Host "Tablero:     http://127.0.0.1:8765"
Write-Host "Ver estado:  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "Logs:        $env:LOCALAPPDATA\TRADING\logs (agent.log, supervisor.log, agente-salida.log)"
Write-Host "Pausa:       crear el archivo vacio $env:LOCALAPPDATA\TRADING\KILL (salir siempre se puede)"
