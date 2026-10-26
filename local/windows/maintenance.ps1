<# Cada 30 min: actualización segura (solo main con CI verde), autorreparación del agente y del
   tablero local, y latido. #>
$Dir = (Resolve-Path "$PSScriptRoot\..\..").Path
$env:NEXUS_HOME = "$env:LOCALAPPDATA\nexus"
$env:PYTHONUTF8 = "1"
$Py = "$Dir\.venv\Scripts\python.exe"
& $Py "$Dir\local\windows\update.py" *>> "$env:NEXUS_HOME\logs\update.log"
try {
  $t = Get-ScheduledTask -TaskName "nexus-agente" -ErrorAction Stop
  if ($t.State -ne "Running") {
    Add-Content "$env:NEXUS_HOME\logs\runner.log" "$(Get-Date -Format s) mantenimiento: la tarea estaba '$($t.State)'; se relanza"
    Start-ScheduledTask -TaskName "nexus-agente"
  }
} catch {
  Add-Content "$env:NEXUS_HOME\logs\runner.log" "$(Get-Date -Format s) mantenimiento: no se pudo verificar la tarea: $_"
}
# Tablero local de solo lectura (http://localhost:8765): tarea propia que se relanza sola, como el
# agente. Se crea acá (y no solo en el instalador) para que llegue a las PCs ya instaladas.
try {
  if (-not (Get-ScheduledTask -TaskName "nexus-dashboard" -ErrorAction SilentlyContinue)) {
    $S = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
      -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
      -MultipleInstances IgnoreNew
    $A = New-ScheduledTaskAction -Execute "powershell.exe" `
      -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dir\local\windows\dashboard.ps1`""
    $T = @((New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"),
           (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)))
    Register-ScheduledTask -TaskName "nexus-dashboard" -Action $A -Trigger $T -Settings $S -Force | Out-Null
    Add-Content "$env:NEXUS_HOME\logs\runner.log" "$(Get-Date -Format s) mantenimiento: se creó la tarea del tablero"
  }
  if ((Get-ScheduledTask -TaskName "nexus-dashboard").State -ne "Running") { Start-ScheduledTask -TaskName "nexus-dashboard" }
  $Mark = "$env:NEXUS_HOME\state\dashboard_shortcut.done"   # el acceso directo se crea una sola vez
  if (-not (Test-Path $Mark)) {
    $Url = Join-Path ([Environment]::GetFolderPath("Desktop")) "nexus tablero.url"
    "[InternetShortcut]`r`nURL=http://localhost:8765/`r`n" | Set-Content -Encoding ascii $Url
    New-Item -ItemType File -Force $Mark | Out-Null
  }
} catch {
  Add-Content "$env:NEXUS_HOME\logs\runner.log" "$(Get-Date -Format s) mantenimiento: no se pudo verificar el tablero: $_"
}
# Mantenimiento cada 10 min (antes 30): si una actualización espera a que el agente quede sin posiciones,
# la ventana sin operar baja de hasta 30 min a hasta 10 (25-oct: 1 h sin entradas con dinero real).
try {
  $mt = Get-ScheduledTask -TaskName "nexus-mantenimiento" -ErrorAction Stop
  if ($mt.Triggers[0].Repetition.Interval -ne "PT10M") {
    $T = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10) -RepetitionInterval (New-TimeSpan -Minutes 10)
    Set-ScheduledTask -TaskName "nexus-mantenimiento" -Trigger $T | Out-Null
    Add-Content "$env:NEXUS_HOME\logs\runner.log" "$(Get-Date -Format s) mantenimiento: ahora corre cada 10 min"
  }
} catch {
  Add-Content "$env:NEXUS_HOME\logs\runner.log" "$(Get-Date -Format s) mantenimiento: no se pudo cambiar su frecuencia: $_"
}
# Latido cada 5 min (comandos de seguridad remotos, chequeo previo y estado para la sesión de Claude).
try {
  if (-not (Get-ScheduledTask -TaskName "nexus-latido" -ErrorAction SilentlyContinue)) {
    $S = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
      -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew
    $A = New-ScheduledTaskAction -Execute "powershell.exe" `
      -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dir\local\windows\latido.ps1`""
    $T = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName "nexus-latido" -Action $A -Trigger $T -Settings $S -Force | Out-Null
    Add-Content "$env:NEXUS_HOME\logs\runner.log" "$(Get-Date -Format s) mantenimiento: se creó la tarea del latido (5 min)"
  }
} catch {
  Add-Content "$env:NEXUS_HOME\logs\runner.log" "$(Get-Date -Format s) mantenimiento: no se pudo crear el latido: $_"
}
& $Py "$Dir\local\windows\report_status.py" *>> "$env:NEXUS_HOME\logs\status.log"
