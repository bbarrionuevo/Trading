<# Latido cada 5 min (tarea "nexus-latido", la crea el mantenimiento): comandos de seguridad de la
   sesión de Claude, chequeo previo del dinero real cada 30 min y publicación del estado en pc-status. #>
$Dir = (Resolve-Path "$PSScriptRoot\..\..").Path
$env:NEXUS_HOME = "$env:LOCALAPPDATA\nexus"
$env:PYTHONUTF8 = "1"
$Py = "$Dir\.venv\Scripts\python.exe"
& $Py "$Dir\local\windows\report_status.py" *>> "$env:NEXUS_HOME\logs\status.log"
