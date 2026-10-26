<# Ejecuta el agente en bucle: si termina (reinicio controlado del actualizador o un error),
   espera 10 s y lo vuelve a lanzar con la versión que esté activa en ese momento.
   Si este bucle muere por cualquier motivo, la tarea "nexus-agente" se relanza sola cada minuto
   (disparador repetitivo con MultipleInstances=IgnoreNew) y el mantenimiento también la verifica. #>
$Dir = (Resolve-Path "$PSScriptRoot\..\..").Path
$env:NEXUS_HOME = "$env:LOCALAPPDATA\nexus"
$env:PYTHONUTF8 = "1"
$Py = "$Dir\.venv\Scripts\python.exe"
$Log = "$env:NEXUS_HOME\logs\runner.log"
function Note($msg) { try { Add-Content $Log "$(Get-Date -Format s) [pid $PID] $msg" } catch {} }
Note "bucle iniciado"
# Un solo agente: si quedó uno huérfano (Stop-ScheduledTask detiene este bucle pero no el proceso de
# Python), se detiene antes de lanzar otro. El 25-oct uno huérfano operó la cuenta real a la vez que el
# nuevo. El agente además toma un lock exclusivo y no arranca si hay otro (defensa doble).
try {
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*nexus.agent.maker_agent*" } |
    ForEach-Object { Note "se detiene un agente huérfano (pid $($_.ProcessId))"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch { Note "no se pudo revisar agentes huérfanos: $_" }
try {
  while ($true) {
    $env:NEXUS_VERSION = (git -C $Dir rev-parse --short HEAD)
    Note "arranca agente version $env:NEXUS_VERSION"
    Push-Location "$Dir\local"
    try {
      $p = Start-Process -FilePath $Py -ArgumentList "-m", "nexus.agent.maker_agent" -NoNewWindow -PassThru `
        -RedirectStandardError "$env:NEXUS_HOME\logs\agent_stderr.log"
      $null = $p.Handle   # sin leer Handle antes de que termine, PowerShell deja ExitCode vacío
      $p.WaitForExit()
      $Code = $p.ExitCode
    } catch {
      $Code = "error al lanzar: $_"
    } finally {
      Pop-Location
    }
    Note "agente terminó (código $Code); reinicio en 10 s"
    Start-Sleep -Seconds 10
  }
} catch {
  Note "el bucle se detuvo por un error: $_"
} finally {
  Note "bucle finalizado"
}
