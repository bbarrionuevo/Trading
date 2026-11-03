# Instalador completo de TRADING en esta PC (Windows 10/11, PowerShell 5.1 o 7).
# Hace todo, en este orden, y se puede volver a correr cuando quieras (actualiza sin borrar nada):
#   1. Instala Python 3.12 y Git con winget si faltan.
#   2. Descarga (o actualiza) el repo con gh en %USERPROFILE%\TRADING.
#   3. Crea el entorno .venv e instala el programa.
#   4. Te pide las claves y las guarda en %LOCALAPPDATA%\TRADING\.env (nunca en el repo).
#   5. Prueba Telegram, Claude y MEXC.
#   6. Instala la tarea programada "trading-agente" (pide permiso de administrador una vez).
# Nunca toca carpetas ni tareas de nexus (REGLAS G-5).
$ErrorActionPreference = "Stop"
$Repo = Join-Path $env:USERPROFILE "TRADING"
$Home_ = Join-Path $env:LOCALAPPDATA "TRADING"
$EnvFile = Join-Path $Home_ ".env"

function Paso($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function RecargarPath {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User")
}

Paso "1/6 Python y Git"
RecargarPath
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  RecargarPath
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
  RecargarPath
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "Falta gh (GitHub CLI): winget install GitHub.cli" }
gh auth status 2>$null
if ($LASTEXITCODE -ne 0) { gh auth login }

Paso "2/6 Descargar el programa en $Repo"
if (Test-Path (Join-Path $Repo ".git")) {
  git -C $Repo checkout -q main
  git -C $Repo pull -q --ff-only
} else {
  gh repo clone bbarrionuevo/Trading $Repo
}

Paso "3/6 Entorno de Python"
$Py = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { py -3.12 -m venv (Join-Path $Repo ".venv") }
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q -e $Repo
if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar el programa" }

Paso "4/6 Claves (Enter para dejar una vacía; las que ya estaban se conservan)"
New-Item -ItemType Directory -Force -Path $Home_ | Out-Null
$actual = @{}
if (Test-Path $EnvFile) {
  foreach ($l in Get-Content $EnvFile) { if ($l -match '^\s*([A-Z_]+)\s*=\s*(.*)$') { $actual[$Matches[1]] = $Matches[2] } }
}
$claves = [ordered]@{
  "TELEGRAM_BOT_TOKEN" = "Token del bot de Telegram (el de @BotFather)"
  "TELEGRAM_CHAT_ID"   = "Tu numero de chat de Telegram"
  "ANTHROPIC_API_KEY"  = "Clave de Claude (la misma que CLAUDE_api_key de GitHub)"
  "MEXC_ACCESS_KEY"    = "MEXC Access Key (SOLO LECTURA)"
  "MEXC_SECRET_KEY"    = "MEXC Secret Key (SOLO LECTURA)"
}
foreach ($k in $claves.Keys) {
  if ($actual[$k]) { Write-Host "$k ya esta cargada."; continue }
  $s = Read-Host -AsSecureString $claves[$k]
  $v = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
  if ($v) { $actual[$k] = $v.Trim() }
}
$lineas = $actual.Keys | Sort-Object | ForEach-Object { "$_=$($actual[$_])" }
[IO.File]::WriteAllText($EnvFile, (($lineas -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($false)))
Write-Host "Guardadas en $EnvFile"

Paso "5/6 Pruebas"
Push-Location $Repo
if ($actual["TELEGRAM_BOT_TOKEN"] -and $actual["TELEGRAM_CHAT_ID"]) { & $Py -m tradingagent telegram-test }
if ($actual["ANTHROPIC_API_KEY"]) { & $Py -m tradingagent llm-check }
& $Py -m tradingagent mexc-check
Pop-Location

Paso "6/6 Tarea programada trading-agente (24/7)"
$tarea = Join-Path $Repo "deploy\windows\instalar-agente.ps1"
Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$tarea`""
Get-ScheduledTask -TaskName "trading-agente" | Select-Object TaskName, State | Format-Table
Write-Host "Listo. Te tiene que llegar por Telegram 'Agente iniciado'. Logs: $Home_\logs\agent.log" -ForegroundColor Green
