# Operación en la PC (Windows)

Este proyecto se llama **TRADING** y vive separado de nexus:

| | TRADING (este proyecto) | nexus (otro proyecto: no se toca) |
|---|---|---|
| Código | `%USERPROFILE%\TRADING` | `%USERPROFILE%\nexus` |
| Datos privados (.env, estado, diario, logs) | `%LOCALAPPDATA%\TRADING` | `%LOCALAPPDATA%\nexus` |
| Variable de entorno | `TRADING_HOME` | `NEXUS_HOME` |
| Tareas programadas | `trading-*` (etapa 5) | `nexus-*` |

Si por error `TRADING_HOME` apunta a una carpeta de nexus, el agente se niega a arrancar.

## Instalación (una vez, en PowerShell)

```powershell
git clone https://github.com/bbarrionuevo/Trading.git $env:USERPROFILE\TRADING
cd $env:USERPROFILE\TRADING
git checkout main                      # o la rama del PR para probarlo antes de fusionar
py -3.12 -m venv .venv                 # Python 3.11 o superior
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest       # tiene que dar todo verde antes de seguir
```

## Telegram (una vez)

1. En Telegram, abrí **@BotFather** → `/newbot` → elegí nombre y usuario. Te da el **token**.
2. Escribile cualquier cosa a tu bot nuevo, por ejemplo `/start`.
3. Creá `%LOCALAPPDATA%\TRADING\.env` con el bloc de notas, partiendo de la plantilla `.env.example`:
   ```
   TELEGRAM_BOT_TOKEN=<el token de BotFather>
   ```
4. Averiguá tu chat:
   ```powershell
   .\.venv\Scripts\python -m tradingagent telegram-chats
   ```
   Te muestra `TELEGRAM_CHAT_ID=...`. Agregá esa línea al `.env`.
5. Probá:
   ```powershell
   .\.venv\Scripts\python -m tradingagent telegram-test
   ```
   Te tiene que llegar "🔔 Prueba de tradingagent…".

**El token es una contraseña:** solo va en el `.env` de `%LOCALAPPDATA%\TRADING`. Nunca en el repo ni en
un chat.

## Fuentes

```powershell
.\.venv\Scripts\python -m tradingagent sources            # cuáles están activas y por qué no las otras
.\.venv\Scripts\python -m tradingagent sources --check    # lee cada una una vez y muestra la noticia más nueva
```

Las de la SEC piden `SEC_USER_AGENT=Tu Nombre tu-mail@dominio` en el `.env` (norma de la SEC). Fuentes
propias, intervalos y apagados van en `%LOCALAPPDATA%\TRADING\config.yaml`; hay un ejemplo en
`config/agent.example.yaml`.

## Correr el agente (paper)

```powershell
.\.venv\Scripts\python -m tradingagent run
```

Al arrancar:

- Llega por Telegram "✅ Agente iniciado · modo PAPER · N/M fuentes activas".
- Cada 15 s aparece en consola el latido (♥ …).
- Cada noticia nueva que pasa los filtros aparece como 📰.

Ctrl+C cierra en orden.

Mientras no exista la etapa 2 (inteligencia), el agente lee, filtra y registra, pero no genera órdenes
solo. El paper se puede ejercitar con el harness:

```powershell
.\.venv\Scripts\python -m tradingagent paper-sim examples\escenario_noticia.jsonl
```

## Parada de emergencia y verificación

- **Pausa (KILL):** creá un archivo vacío `%LOCALAPPDATA%\TRADING\KILL`. Con ese archivo no se abren
  posiciones. Para reanudar, borralo.
- **Diario:** `.\.venv\Scripts\python -m tradingagent journal-verify` dice si alguien lo alteró.
- **Estado:** `%LOCALAPPDATA%\TRADING\state\heartbeat.json`. Logs en `%LOCALAPPDATA%\TRADING\logs\agent.log`.
