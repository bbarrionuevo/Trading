# Operación en la PC (Windows)

Este proyecto se llama **TRADING** y vive separado de nexus:

| | TRADING (este proyecto) | nexus (otro proyecto: no se toca) |
|---|---|---|
| Código | `%USERPROFILE%\TRADING` | `%USERPROFILE%\nexus` |
| Datos privados (.env, estado, diario, logs) | `%LOCALAPPDATA%\TRADING` | `%LOCALAPPDATA%\nexus` |
| Variable de entorno | `TRADING_HOME` | `NEXUS_HOME` |
| Tareas programadas | `trading-*` (etapa 5) | `nexus-*` |

Si por error `TRADING_HOME` apunta a una carpeta de nexus, el agente se niega a arrancar.

## Instalación automática (recomendada)

En PowerShell (no hace falta que sea como administrador; al final pide permiso una vez):

```powershell
gh api repos/bbarrionuevo/Trading/contents/deploy/windows/instalar.ps1 -H "Accept: application/vnd.github.raw" | Out-String | Invoke-Expression
```

Instala Python y Git si faltan, descarga el repo en `%USERPROFILE%\TRADING`, crea el entorno, te pide las
claves (las guarda en `%LOCALAPPDATA%\TRADING\.env`), prueba Telegram, Claude y MEXC, y deja la tarea
`trading-agente` corriendo 24/7. Para actualizar, se vuelve a correr el mismo comando.

## Instalación a mano (una vez, en PowerShell)

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

- Llega por Telegram "✅ Agente iniciado · modo PAPER · N/M fuentes activas", y la primera vez
  "🧪 Operador paper EN VIVO · mexc-futuros · 120 USDT simulados".
- Cada 15 s aparece en consola el latido (♥ …).
- Cada noticia nueva que pasa los filtros aparece como 📰 y se evalúa en el momento.

Ctrl+C cierra en orden.

**Qué hace solo** (`live.py`, el mismo núcleo que usa GitHub Actions):

1. Clasifica cada noticia en microsegundos. Si la noticia es dudosa y hay clave de Claude, le pregunta a
   Claude Haiku 5.5 (máximo 8 s y 5 USD por día).
2. Calcula el vector de evento y el alfa, que vale la mitad cada 120 s, y el meta-modelo (p ≥ 0,60).
3. Calcula la triple barrera por volatilidad y los costos, y el tamaño por ¼-Kelly.
4. Revisa el libro real: spread y su z-score, profundidad, L3, divergencia y lambda de Kyle.
5. Abre en paper: largo o corto en futuros de MEXC a 1x. Si algo falla, no entra, y el motivo queda
   registrado.
6. Cada segundo vigila stop, take-profit y expiración contra el libro real. Sale también si llega una
   noticia en contra.
7. Frenos: si pierde 2 % en el día o cae 8 % desde el máximo, cierra todo. Tres pérdidas seguidas: una
   hora de pausa.
8. Cada 15 min, en segundo plano, mide la reacción del precio y aprende. El aprendizaje solo apaga lo
   que pierde: nunca agranda nada.

Todo queda en `%LOCALAPPDATA%\TRADING\paper-vivo\mexc-futuros\`, con `estado.json`, `operaciones/`
y `decisiones/` (cada noticia con su análisis y su motivo). Un reinicio no borra nada.

El paper también se puede ejercitar con el harness:

```powershell
.\.venv\Scripts\python -m tradingagent paper-sim examples\escenario_noticia.jsonl
```

## 24/7 en la PC (recomendado: es lo que captura el alfa)

Con el cron de GitHub la noticia llega minutos tarde y el alfa ya se fue. En la PC, prendida y conectada,
el agente reacciona en segundos. Para que arranque solo con Windows y se reinicie si se cae:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\TRADING\deploy\windows\instalar-agente.ps1"
```

Correlo una vez, en PowerShell **como administrador**. Instala la tarea programada `trading-agente`
(nunca `nexus-*`), que arranca con Windows y al iniciar sesión y se reinicia al minuto si se cae. Para
quitarla: `deploy\windows\quitar-agente.ps1`.

**Claves en `%LOCALAPPDATA%\TRADING\.env`** (plantilla en `.env.example`):

| Clave | Para qué | Obligatoria |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Avisos | Sí, para enterarte |
| `ANTHROPIC_API_KEY` | Claude para las noticias dudosas | No (sin ella decide el clasificador local) |
| `MEXC_ACCESS_KEY`, `MEXC_SECRET_KEY` | Leer las comisiones reales de tu cuenta (D-5) | No (sin ellas usa las públicas) |

Las claves de MEXC tienen que ser **de solo lectura**: en MEXC, al crear la API, sin permiso de trading
ni de retiro. El programa no tiene código para mandar órdenes ni retirar: el test `test_a3` lo verifica
en cada cambio.

**Cambiar de mercado:** en `%LOCALAPPDATA%\TRADING\config.yaml` poné `trader: {mercado: binance-spot}`
(o `enabled: false` para que solo lea y registre).

**Diagnóstico de MEXC:** `.\.venv\Scripts\python -m tradingagent mexc-check` muestra contratos,
comisiones públicas y funding, y si las claves leen la cuenta. Nunca muestra saldos.

## Parada de emergencia y verificación

- **Pausa (KILL):** creá un archivo vacío `%LOCALAPPDATA%\TRADING\KILL`. Con ese archivo no se abren
  posiciones. Para reanudar, borralo.
- **Diario:** `.\.venv\Scripts\python -m tradingagent journal-verify` dice si alguien lo alteró.
- **Estado:** `%LOCALAPPDATA%\TRADING\state\heartbeat.json`. Logs en `%LOCALAPPDATA%\TRADING\logs\agent.log`.

## Entrenamiento en la nube (GitHub Actions, sin la PC)

Un workflow junta los datos con los que aprende el agente (`.github/workflows/recoleccion.yml`). Es el
respaldo de la PC: aunque la PC esté apagada, los datos se siguen juntando y el paper por corridas se
sigue midiendo. GitHub a veces demora el cron (el 2-nov tardó horas en dar la primera corrida
programada). En cada corrida:

1. Lee las fuentes y guarda las noticias nuevas de hasta 3 horas en la rama `data`. Las copias de otras
   fuentes y lo que parece ruido también quedan, marcados. Cada noticia lleva su clasificación (`nlp`), y
   las dudosas la opinión de Claude (`nlp_llm`).
2. Una hora después de cada noticia, mide la reacción del precio con velas públicas de 1 minuto
   (Binance; si no responde, Coinbase) a 1, 5, 15 y 60 minutos desde la publicación, y la triple barrera
   desde la llegada. Con eso aprende: apaga lo que pierde y ajusta el meta-modelo (solo reduce riesgo).
3. Opera en **paper**, cada 5 minutos, en dos cuentas simuladas de 120 USDT: Binance spot (solo largos)
   y futuros de MEXC (largos y cortos a 1x). Todo queda en `paper/<mercado>/` de la rama `data`. Con el
   cron, la noticia llega minutos tarde: casi siempre el alfa ya se fue y no entra. Es lo esperable: la
   evidencia en serio sale de la PC.
4. Por Telegram:
   - un aviso al activarse;
   - las noticias de la Fed y la SEC;
   - cada operación paper con su SL, TP y p, y cada salida con su motivo y su retorno neto;
   - cada freno;
   - un resumen por día con una línea por cuenta paper.

Nunca hay órdenes reales (REGLAS G-6): el código no las tiene. Para frenar solo las entradas del paper,
subí un archivo vacío `KILL` a la rama `data`.

**Secretos** (en GitHub: Settings → Secrets and variables → Actions):

| Secreto | Para qué | Estado |
|---|---|---|
| `CHARBIS_BOT_TOKEN` | El bot que manda los avisos | ✅ cargado |
| `TELEGRAM_CHAT_ID` | A qué chat los manda | Falta: ver los pasos de abajo |
| `SEC_USER_AGENT` | Habilita las dos fuentes de la SEC. Formato: `Nombre Apellido tu-mail@dominio` (norma de la SEC) | Opcional |
| `CLAUDE_API_KEY` | Claude Haiku 5.5 para las noticias dudosas (tope diario del sobre: 5 USD) | Opcional |
| `ACCESS_KEY_MEXC`, `SECRET_KEY_MEXC` | Leer las comisiones reales de tu cuenta de MEXC (REGLAS D-5). **Solo lectura**: sin permiso de trading ni de retiro | Opcional |

GitHub guarda los nombres de los secretos en mayúsculas: `CLAUDE_api_key` y `CLAUDE_API_KEY` son el
mismo. Las claves de MEXC solo leen: el programa no tiene código de órdenes ni de retiros (REGLAS A-3).
El CI rechaza un workflow que use cualquier secreto fuera de esta tabla.

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
