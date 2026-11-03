# Instalador completo de TRADING en esta PC (Windows 10/11, PowerShell 5.1 o 7).
# Hace todo, en este orden, y se puede volver a correr cuando quieras (actualiza sin borrar nada):
#   1. Instala Python 3.12 y Git con winget si faltan.
#   2. Descarga (o actualiza) el repo con gh en %USERPROFILE%\TRADING.
#   3. Crea el entorno .venv e instala el programa (con lo necesario para correr los tests en la PC).
#   4. Te pide las claves que falten y las guarda en %LOCALAPPDATA%\TRADING\.env (nunca en el repo).
#      GITHUB_TOKEN_PC se toma de gh: con eso el supervisor publica el estado de la PC (rama pc, G-8).
#   5. Prueba Telegram, Claude y MEXC.
#   6. Instala la tarea programada "trading-agente" (pide permiso de administrador una vez): el
#      supervisor mantiene al agente 24/7, lo actualiza solo y sirve el tablero.
#   7. Crea el acceso directo "TRADING tablero" en el escritorio y abre el tablero.
# Nunca toca carpetas ni tareas de nexus (REGLAS G-5). Solo caracteres ASCII (la consola de Windows
# muestra mal los acentos de un script sin BOM).
$ErrorActionPreference = "Stop"
$Repo = Join-Path $env:USERPROFILE "TRADING"
$Home_ = Join-Path $env:LOCALAPPDATA "TRADING"
$EnvFile = Join-Path $Home_ ".env"
$Pagina = Join-Path $Home_ "tablero.html"            # la escribe el supervisor: redirige al puerto real
$Direccion = Join-Path $Home_ "state\tablero.json"   # la direccion real (si 18765 esta ocupado, otro puerto)

function Paso($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function RecargarPath {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User")
}

Paso "1/7 Python y Git"
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

Paso "2/7 Descargar el programa en $Repo"
if (Test-Path (Join-Path $Repo ".git")) {
  git -C $Repo fetch -q https://github.com/bbarrionuevo/Trading.git "+refs/heads/main:refs/remotes/origin/main"
  if ($LASTEXITCODE -ne 0) { throw "No pude bajar la version nueva (sin internet?)" }
  git -C $Repo checkout -q -f -B main origin/main
} else {
  gh repo clone bbarrionuevo/Trading $Repo
}
if ($LASTEXITCODE -ne 0) { throw "No pude descargar el programa" }

Paso "3/7 Entorno de Python"
$Py = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { py -3.12 -m venv (Join-Path $Repo ".venv") }
& $Py -m pip install -q --disable-pip-version-check --upgrade pip
& $Py -m pip install -q --disable-pip-version-check -e "$($Repo)[dev]"
if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar el programa" }

Paso "4/7 Claves (Enter deja una vacia; las que ya estaban se conservan)"
New-Item -ItemType Directory -Force -Path $Home_ | Out-Null
$actual = @{}
if (Test-Path $EnvFile) {
  foreach ($l in Get-Content $EnvFile) {
    if ($l -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') { $actual[$Matches[1]] = $Matches[2] }
  }
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
if ($actual["SEC_USER_AGENT"]) {
  Write-Host "SEC_USER_AGENT ya esta cargada."
} else {
  $v = Read-Host "SEC_USER_AGENT (opcional: activa 2 fuentes oficiales de la SEC). Tu nombre y un email de contacto, ej: TRADING Juan juan@correo.com"
  if ($v) { $actual["SEC_USER_AGENT"] = $v.Trim() }
}
$tok = ""
try { $tok = (gh auth token 2>$null | Out-String).Trim() } catch { $tok = "" }
if ($tok) {
  $actual["GITHUB_TOKEN_PC"] = $tok
  Write-Host "GITHUB_TOKEN_PC tomado de gh: el supervisor publica el estado de la PC para Claude (rama pc)."
} else {
  Write-Host "No pude leer el token de gh: el estado de la PC no se va a publicar (lo demas anda igual)." -ForegroundColor Yellow
}
$lineas = $actual.Keys | Sort-Object | ForEach-Object { "$_=$($actual[$_])" }
[IO.File]::WriteAllText($EnvFile, (($lineas -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($false)))
Write-Host "Guardadas en $EnvFile"

Paso "5/7 Pruebas"
Push-Location $Repo
if ($actual["TELEGRAM_BOT_TOKEN"] -and $actual["TELEGRAM_CHAT_ID"]) { & $Py -m tradingagent telegram-test }
if ($actual["ANTHROPIC_API_KEY"]) { & $Py -m tradingagent llm-check }
& $Py -m tradingagent mexc-check
Pop-Location

Paso "6/7 Tarea programada trading-agente (24/7, se actualiza sola)"
$tarea = Join-Path $Repo "deploy\windows\instalar-agente.ps1"
$inicio = Get-Date
Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$tarea`""
Get-ScheduledTask -TaskName "trading-agente" | Select-Object TaskName, State | Format-Table

Paso "7/7 Tablero"
$Atajo = Join-Path ([Environment]::GetFolderPath("Desktop")) "TRADING tablero.url"
$Destino = "file:///" + ($Pagina -replace '\\', '/')
[IO.File]::WriteAllText($Atajo, "[InternetShortcut]`r`nURL=$Destino`r`n", (New-Object Text.ASCIIEncoding))
Write-Host "Acceso directo creado: $Atajo"
$url = $null
for ($i = 0; $i -lt 45 -and -not $url; $i++) {
  # Solo la direccion que escribio el supervisor recien arrancado, y solo si responde ESTE tablero
  # (en el 8765 de siempre hay otro programa tuyo).
  if ((Test-Path $Direccion) -and (Get-Item $Direccion).LastWriteTime -gt $inicio) {
    try {
      $u = (Get-Content $Direccion -Raw | ConvertFrom-Json).url
      $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 ($u + "api/estado")
      if ($r.Content -match "estado_texto") { $url = $u }
    } catch { }
  }
  if (-not $url) { Start-Sleep -Seconds 2 }
}
if ($url) { Start-Process $url; Write-Host "Tablero: $url" } else {
  Write-Host "El tablero todavia no responde: abri el acceso directo 'TRADING tablero' en un minuto." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Listo. El supervisor mantiene al agente 24/7 y lo actualiza solo (solo versiones con CI verde)." -ForegroundColor Green
Write-Host "Tablero: acceso directo 'TRADING tablero'   Logs: $Home_\logs   Pausa: crear el archivo $Home_\KILL"
