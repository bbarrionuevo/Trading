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

## Entrenamiento en la nube (GitHub Actions, sin la PC)

Mientras el agente no corre en la PC, un workflow junta los datos con los que se entrena la etapa 2
(`.github/workflows/recoleccion.yml`). Corre cada 15 minutos, a los :04, :19, :34 y :49 (GitHub a veces
lo demora unos minutos), y en cada corrida:

1. Lee las fuentes y guarda las noticias nuevas de hasta 3 horas en la rama `data`. Las copias de otras
   fuentes y lo que parece ruido también quedan, marcados.
2. Una hora después de cada noticia, mide la reacción del precio con velas públicas de 1 minuto
   (Binance; si no responde, Coinbase) a 1, 5, 15 y 60 minutos desde la publicación.
3. Por Telegram: un aviso al activarse, las noticias de la Fed y la SEC, y un resumen por día.

No opera, ni en paper (REGLAS G-6).

**Secretos** (en GitHub: Settings → Secrets and variables → Actions):

| Secreto | Para qué | Estado |
|---|---|---|
| `CHARBIS_BOT_TOKEN` | El bot que manda los avisos | ✅ cargado |
| `TELEGRAM_CHAT_ID` | A qué chat los manda | Falta: ver los pasos de abajo |
| `SEC_USER_AGENT` | Habilita las dos fuentes de la SEC. Formato: `Nombre Apellido tu-mail@dominio` (norma de la SEC) | Opcional |

Las claves de exchange **no se usan**: las velas públicas no las necesitan (REGLAS G-1). El CI rechaza un
workflow que use cualquier secreto fuera de esa tabla.

**Averiguar `TELEGRAM_CHAT_ID`** (una vez):

1. En Telegram, abrí tu bot y mandale `/id`.
2. En GitHub: Actions → **Telegram · averiguar mi chat** → Run workflow.
3. El bot te contesta "🔑 Tu TELEGRAM_CHAT_ID es …". En el log, que es público, solo queda cuántos avisos
   mandó, nunca el número.
4. Guardá ese número como secreto `TELEGRAM_CHAT_ID`. En el siguiente cuarto de hora llega
   "✅ Recolector de TRADING activo".

Otra forma: @userinfobot te dice tu número. Igual tenés que mandarle `/start` a tu bot, porque un bot no
puede escribirle a alguien que nunca le escribió.

Si el workflow falla, el log dice a qué bot se conectó y cuántos mensajes vio:

- **"devolvió 0 mensajes"**: otro programa que usa el mismo bot (otras alertas, otro proyecto) se lleva los
  mensajes antes. Usá @userinfobot.
- **"Ningún chat privado le mandó /id a @…"**: el `/id` fue a otro bot. Revisá el nombre que muestra el log.

**Dónde quedan los datos:** en la rama `data`:

| Archivo | Contenido |
|---|---|
| `eventos/AAAA-MM.jsonl` | Las noticias |
| `reacciones/AAAA-MM.jsonl` | Las reacciones del precio |
| `estado.json` | La salud de cada fuente y los conteos por día |
| `LEEME.md` | Qué es cada campo |

**Pausar:** Actions → Recolector de entrenamiento → ⋯ → Disable workflow. **Correrlo ya:** Run workflow.

**A tener en cuenta:**

- El repo es público: los minutos de Actions son gratis, pero los logs y la rama `data` están a la vista.
  Por eso solo hay datos públicos (titulares, enlaces, fragmentos cortos y precios).
- GitHub puede pausar los workflows programados de un repo público después de 60 días sin actividad.
  Si pasa, llega un mail y se reactiva con Enable workflow.
- Algunas fuentes pueden bloquear a los servidores de GitHub. `estado.json` muestra la salud de cada una,
  y el resumen diario lista las que fallan.
- En la PC se puede correr lo mismo a mano: `.\.venv\Scripts\python -m tradingagent collect` (guarda en
  `%LOCALAPPDATA%\TRADING\datos`).
