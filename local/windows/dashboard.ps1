<# Tablero local de solo lectura en http://localhost:8765 (solo responde en esta PC).
   Corre en bucle: si termina (versión nueva instalada o un error), espera 10 s y lo vuelve a lanzar
   con el código que esté activo. La tarea "nexus-dashboard" lo relanza cada minuto si este bucle muere
   (MultipleInstances=IgnoreNew) y el mantenimiento crea esa tarea si no existe. #>
$Dir = (Resolve-Path "$PSScriptRoot\..\..").Path
$env:NEXUS_HOME = "$env:LOCALAPPDATA\nexus"
$env:PYTHONUTF8 = "1"
$Py = "$Dir\.venv\Scripts\python.exe"
$Log = "$env:NEXUS_HOME\logs\dashboard.log"
function Note($msg) { try { Add-Content $Log "$(Get-Date -Format s) [pid $PID] $msg" } catch {} }
Note "bucle del tablero iniciado"
try {
  while ($true) {
    Push-Location "$Dir\local"
    try {
      $p = Start-Process -FilePath $Py -ArgumentList "-m", "nexus.dashboard", "8765" -NoNewWindow -PassThru `
        -RedirectStandardError "$env:NEXUS_HOME\logs\dashboard_stderr.log"
      $null = $p.Handle   # sin leer Handle antes de que termine, PowerShell deja ExitCode vacío
      $p.WaitForExit()
      $Code = $p.ExitCode
    } catch {
      $Code = "error al lanzar: $_"
    } finally {
      Pop-Location
    }
    Note "tablero terminó (código $Code); reinicio en 10 s"
    Start-Sleep -Seconds 10
  }
} catch {
  Note "el bucle del tablero se detuvo por un error: $_"
}
