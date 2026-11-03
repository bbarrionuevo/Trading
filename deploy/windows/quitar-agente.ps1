# Detiene y borra la tarea programada "trading-agente". No borra datos (estado, diario, logs).
# Nunca toca tareas de nexus (REGLAS G-5).
$ErrorActionPreference = "Stop"
$TaskName = "trading-agente"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  # Si quedo un agente suelto, se le pide cerrar en orden (lo ve en menos de 1 s).
  $Estado = Join-Path $env:LOCALAPPDATA "TRADING\state"
  if (Test-Path $Estado) { Set-Content -Path (Join-Path $Estado "parar") -Value "quitar-agente" -Encoding ascii }
  Write-Host "La tarea '$TaskName' fue detenida y quitada. Los datos siguen en $env:LOCALAPPDATA\TRADING."
} else {
  Write-Host "No hay ninguna tarea '$TaskName' instalada."
}
