# Instrucciones para la sesión de Claude en el PC del dueño (Windows 11)

Esta sesión tiene acceso al PC. La sesión en la nube (desarrollo e investigación) no puede verla,
así que **el repositorio es el canal**: aquí recibís tareas y en la rama `pc-sesion` dejás los
resultados. Antes de actuar, leé `docs/REGLAS.md`: esas reglas mandan.

## Dónde está todo
- Despliegue: `%USERPROFILE%\nexus` (clon aparte que sigue `main`; no edites código ahí).
- Estado y logs: `%LOCALAPPDATA%\nexus` (`state\status.json`, `state\update.json`, `logs\*.log`).
- Claves: `%LOCALAPPDATA%\nexus\.env`. **Nunca muestres, copies ni subas su contenido.**
- Tareas programadas: `nexus-agente` (agente, se relanza solo) y `nexus-mantenimiento` (cada 10 min).

## Qué podés hacer sin pedir permiso
- Diagnosticar: estado de las tareas, logs, `status.json`, versión instalada (`git -C %USERPROFILE%\nexus log --oneline -1`).
- Reparar lo operativo: relanzar tareas (`Start-ScheduledTask`), reinstalar con
  `local\windows\install.ps1` si algo quedó roto, sincronizar la hora de Windows.
- Ejecutar comandos de **solo lectura** de nexus, por ejemplo `python -m nexus.check_keys` o
  `python -m nexus.transfers BMT,IOST,DCR` (desde `%USERPROFILE%\nexus\local` con `..\.venv\Scripts\python`).
- Publicar resultados en la rama `pc-sesion` (ver abajo).

## Qué NO podés hacer nunca
- Escribir la frase de aprobación de `update.py --aprobar` ni la de `risk --rearmar`. Esas
  confirmaciones son **del dueño, a mano**: es el control que impide que una IA cambie límites o
  el actualizador sin que él lo vea. No las automatices (ni con `echo` ni con tuberías).
- Cambiar `live`, límites de riesgo, permisos de claves o activar operaciones reales. En particular,
  **nunca ejecutes `python -m nexus.agent.authorize`** (ni con `--revocar`): la autorización para
  operar con dinero real la escribe el dueño a mano. Tampoco escribas en el archivo `KILL` salvo que
  el dueño te lo pida en el chat en ese momento. (El canal de issues `pc-comando` es de la sesión en
  la nube y lo ejecuta el latido; vos no lo uses ni lo edites.)
- Editar archivos del despliegue para "arreglar" algo: los cambios se hacen por PR a `main`.
- Mostrar o copiar secretos en el chat, en logs o en la rama `pc-sesion`.

## Cómo informar a la sesión en la nube
Escribí un archivo Markdown con fecha y resultados (sin secretos) y subilo a la rama `pc-sesion`.
Desde la carpeta del despliegue:
```powershell
$d = "$env:TEMP\nexus-pcsesion"; Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue
git clone -q --branch pc-sesion https://github.com/bbarrionuevo/nexus.git $d 2>$null
if (-not $?) { git clone -q https://github.com/bbarrionuevo/nexus.git $d; git -C $d checkout -q --orphan pc-sesion; git -C $d rm -rqf . }
New-Item -ItemType Directory -Force "$d\reportes" | Out-Null
Set-Content -Encoding utf8 "$d\reportes\$(Get-Date -Format yyyyMMdd-HHmm).md" "<tu reporte aquí>"
git -C $d add reportes; git -C $d commit -qm "reporte del PC"; git -C $d push -q origin HEAD:pc-sesion
```

## Tareas pendientes (las actualiza la sesión en la nube)
Hechas el 24-oct-2026 (`reportes/20261024-1250.md`): estado de tareas, transferencias de 9 monedas y
latencia. Gracias: el informe fue muy útil.

1. Confirmar que el actualizador instaló la versión aprobada (`state\update.json` con `activada`).
   Si quedó `pospuesta` solo por posiciones abiertas, esperar: el agente deja de abrir y se vacía solo.
2. Registrar el disparador de 1 minuto de `nexus-agente`: **solo si** la versión instalada es igual a
   `origin/main` (`git -C %USERPROFILE%\nexus rev-parse HEAD` contra `git -C %USERPROFILE%\nexus rev-parse origin/main`
   después de `git fetch`), correr `local\windows\install.ps1`. Si no son iguales, no reinstalar
   (saltearía la aprobación local) y anotarlo en el reporte.
3. Con la versión nueva instalada (ya trae `nexus.transfers`), correr
   `python -m nexus.transfers CYBER,ANIME,LUNA,ID,AMP,POLYX,OPEN,IMX,ATM` y subir el resultado.
4. ATM: comparar en la salida cruda de `capital/config` de ambos exchanges la red `CHZ` (MEXC) y `CHZ2`
   (Binance): nombre completo y dirección de contrato si aparece. Solo informar; no agregar alias.
