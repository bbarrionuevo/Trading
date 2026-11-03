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
claves que falten (las guarda en `%LOCALAPPDATA%\TRADING\.env`), prueba Telegram, Claude y MEXC, deja la
tarea `trading-agente` corriendo 24/7 con el supervisor, crea el acceso directo **TRADING tablero** en el
escritorio y abre el tablero. **Se corre una sola vez:** desde ahí el programa se actualiza solo. Volver a
correrlo no rompe nada (sirve para cargar una clave nueva).

## Qué hace solo, sin que toques nada

- **Siempre listo:** el supervisor (`python -m tradingagent supervisar`) corre al agente y lo relanza al
  instante si se cae. Si se cae 3 veces en 10 minutos, te avisa por Telegram. Arranca con Windows, al
  iniciar sesión y, por las dudas, la tarea lo revisa cada minuto (nunca corren dos a la vez).
- **Se actualiza solo (REGLAS G-7):** cada 15 minutos mira si hay una versión nueva en `main`. Solo la toma
  si los tests de GitHub dieron verde, y espera a que no haya posiciones abiertas (como mucho 6 h). Para al
  agente con calma (posiciones y caja quedan guardadas), actualiza, prueba que arranque y lo vuelve a
  prender en segundos. Si la versión nueva no arranca, vuelve a la anterior y te avisa. Cada actualización
  te llega por Telegram ("🔄 Actualicé TRADING …").
- **Se prueba en tu PC:** después de cada actualización corre todos los tests en segundo plano (prioridad
  baja, sin tus claves y en una carpeta aparte). El resultado se ve en el tablero.
- **Claude ve cómo va (REGLAS G-8):** cada 15 minutos publica en la rama `pc` del repo el estado de la PC
  (solo paper y salud: nunca claves, el `.env` ni saldos reales). Así se evalúan las operaciones y se
  mejora el programa sin pedirte capturas. Usa `GITHUB_TOKEN_PC`, que el instalador toma de `gh`.

## Tablero

Acceso directo **TRADING tablero** en el escritorio (por defecto **http://127.0.0.1:18765**; si otro
programa usa ese puerto, el supervisor toma el siguiente libre y el acceso directo lo sigue). Se actualiza cada 5
segundos y muestra: si el agente está listo para operar, la cuenta paper (equity, PnL, operaciones,
acierto, evidencia para dinero real), la curva de equity, las posiciones abiertas con su stop y
take-profit, las últimas operaciones y decisiones (y **por qué no entra**), la salud (fuentes, noticias,
velocidad, Telegram, IA y su gasto del día), las actualizaciones, los tests en tu PC y los últimos errores.
Es de solo lectura y solo se puede abrir desde tu PC. Sin el supervisor: `python -m tradingagent tablero
--abrir`.

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

Qué lee: la Reserva Federal; medios cripto (CoinDesk, Cointelegraph, Decrypt, The Block); bolsa y materias
primas (CNBC titulares y resultados, Seeking Alpha, Investing.com); y el **anuncio oficial de listados de
Binance**, leído cada 10 segundos (la noticia que más mueve un precio: hay que llegar en el primer minuto). OKX y
Bitget están apagados porque sus listados no movieron el precio (`docs/ESTUDIO-LISTADOS.md`).

Las de la SEC piden `SEC_USER_AGENT=Tu Nombre tu-mail@dominio` en el `.env` (norma de la SEC). Fuentes
propias, intervalos y apagados van en `%LOCALAPPDATA%\TRADING\config.yaml`; hay un ejemplo en
`config/agent.example.yaml`.

## Correr el agente (paper)

```powershell
.\.venv\Scripts\python -m tradingagent run
```

Al arrancar:

- Llega por Telegram "✅ TRADING está funcionando · práctica (sin plata real)", y la primera vez
  "🧪 Empecé la cuenta de práctica en futuros MEXC con 120 USDT simulados".
- Cada 15 s aparece en consola el latido (♥ …).
- Cada noticia nueva que pasa los filtros aparece como 📰 y se evalúa en el momento.

Ctrl+C cierra en orden.

**Qué te llega por Telegram desde la PC** (siempre en castellano simple, sin siglas):

- Cada operación: qué hizo ("Compré BTC" / "Vendí en corto ETH"), con cuánto, a qué precio sale si
  pierde y cuánto perdería, a qué precio cobra y cuánto ganaría, cuánto tiempo como mucho y por qué.
- Cada cierre: cuánto ganó o perdió (neto de comisiones) y por qué cerró.
- Cada freno de seguridad: qué pasó, qué hace el programa y si tenés que hacer algo (casi nunca).
- Un resumen por día a las 21:00 (hora argentina): operaciones, resultado, cómo está la cuenta, cuánto
  falta para dinero real y el gasto de la IA.
- Avisos del supervisor: cuando se actualiza o si se cayó y lo levantó.

**Qué hace solo** (`live.py`, el mismo núcleo que usa GitHub Actions):

1. Clasifica cada noticia en microsegundos, sin costo. A Claude Haiku 5.5 le pregunta **solo cuando la
   respuesta puede cambiar lo que se hace**: la noticia es dudosa (rumor, desmentido) o describe un hecho
   concreto que las reglas no reconocen, está fresca, no hay un freno que impida entrar y queda lugar para
   operar (o hay una posición abierta de ese activo). Preguntas, opiniones y análisis no se consultan, y el
   mismo titular no se paga dos veces. No hay un tope que la frene cuando hace falta: solo un techo de
   seguridad (el del sobre, 5 USD por día) contra un error que se dispare. En la práctica son centavos por
   mes. El tablero muestra cuántas consultas se hicieron y cuántas se evitaron.
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
el agente reacciona en segundos. El instalador ya deja la tarea; para reinstalar solo la tarea:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\TRADING\deploy\windows\instalar-agente.ps1"
```

Correlo en PowerShell **como administrador**. Instala la tarea programada `trading-agente` (nunca
`nexus-*`), que corre el supervisor al arrancar Windows, al iniciar sesión y cada minuto si no está
corriendo. Para quitarla: `deploy\windows\quitar-agente.ps1`. Logs en `%LOCALAPPDATA%\TRADING\logs`:
`agent.log` (el agente), `supervisor.log` (reinicios y actualizaciones), `agente-salida.log` (su consola)
y `tests.txt` (los tests en tu PC).

**Claves en `%LOCALAPPDATA%\TRADING\.env`** (plantilla en `.env.example`):

| Clave | Para qué | Obligatoria |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Avisos | Sí, para enterarte |
| `ANTHROPIC_API_KEY` | Claude para las noticias dudosas | No (sin ella decide el clasificador local) |
| `MEXC_ACCESS_KEY`, `MEXC_SECRET_KEY` | Leer las comisiones reales de tu cuenta (D-5) | No (sin ellas usa las públicas) |
| `SEC_USER_AGENT` | Prende 2 fuentes oficiales de la SEC (comunicados y 8-K): tu nombre y un email | No |
| `GITHUB_TOKEN_PC` | Publicar el estado de la PC para Claude (rama `pc`); el instalador lo toma de `gh` | No |

Las claves de MEXC tienen que ser **de solo lectura**: en MEXC, al crear la API, sin permiso de trading
ni de retiro. El programa no tiene código para mandar órdenes ni retirar: el test `test_a3` lo verifica
en cada cambio.

**Cambiar de mercado:** en `%LOCALAPPDATA%\TRADING\config.yaml` poné `trader: {mercado: binance-spot}`
(o `enabled: false` para que solo lea y registre).

**Diagnóstico de MEXC:** `.\.venv\Scripts\python -m tradingagent mexc-check` muestra contratos,
comisiones públicas y funding, y si las claves leen la cuenta. Nunca muestra saldos.

## Dinero real (futuros de MEXC)

El plan completo y lo que tiene que hacer el dueño está en `docs/DINERO-REAL.md`. En resumen, en la PC.

**Primero, sin plata (simulacro):** el mismo operador real, con los precios reales de MEXC, pero sin mandar
ninguna orden. No pide frase ni claves:

```powershell
cd $env:USERPROFILE\TRADING; .\.venv\Scripts\python -m tradingagent armar-real --simulacro --capital 100 --dias 2
```

Cada aviso de Telegram dice "SIMULACRO de dinero real (sin plata)". En el tablero aparece como
"🧪 Simulacro del dinero real".

**Después, con plata:**

```powershell
cd $env:USERPROFILE\TRADING; .\.venv\Scripts\python -m tradingagent mexc-real-check
```

Es una prueba sin riesgo: pone órdenes que no se pueden ejecutar, verifica cómo las entiende MEXC y las
cancela. Si da todo OK:

```powershell
cd $env:USERPROFILE\TRADING; .\.venv\Scripts\python -m tradingagent armar-real
```

Opera con **todo el saldo** de la cuenta de futuros (sin tope) y pide escribir una frase exacta
(`ARMO DINERO REAL CON TODO EL SALDO POR 30 DIAS`). Para frenar: `armar-real --desarmar` (no abre nada nuevo) o
`python -m tradingagent cerrar-todo` (cancela, cierra todo lo real y desarma).

## Vigilancia de Claude (con dinero real)

El dueño pidió que Claude vigile "prácticamente de forma sincrónica" cuando opere con plata real. Hay tres
niveles:

1. **Rama `pc` (automática):** el supervisor publica la práctica, el simulacro, la salud y los errores cada
   **2 minutos** si hay algo armado o posiciones abiertas (si no, cada 15). La cuenta de dinero real **no se
   publica** (REGLAS G-8). La práctica toma las mismas decisiones con las mismas noticias, y los avisos del real
   (órdenes rechazadas, conciliación, "MEXC no responde") quedan en `errores.jsonl`.
2. **Tareas programadas de Claude:** una revisión completa cada día a las 7:36 y una vigilancia corta cada 2
   horas, de 8 a 24 (hora argentina). Solo avisan al celular si hay algo. Corren sin el conector de GitHub, así
   que si encuentran un arreglo lo dejan en una rama `claude/...` y te avisan para que abras el PR. Para que
   también abran y fusionen PRs solos, agregales el conector de GitHub en claude.ai → Rutinas.
3. **Acceso directo a la PC (para corregir en el momento):** en la app de Claude de la PC, en Configuración →
   Claude Code → Remote Control, agregá la carpeta `%USERPROFILE%\TRADING`. O abrí una terminal en esa carpeta y
   corré `claude remote-control`. Después avisale a Claude: va a pedir conectarse y vos lo aprobás. Con eso
   puede ver los registros y la cuenta real ahí mismo y correr `armar-real --desarmar` o `cerrar-todo` si hace
   falta.

## Órdenes por Telegram (desde el celular)

Mandale al bot (solo obedece a tu chat):

| Orden | Qué hace |
|---|---|
| `/estado` | Cómo va: práctica, simulacro o dinero real (con montos: Telegram es privado), posiciones y frenos |
| `/pausa` | No abre nada nuevo. Lo abierto sigue con sus límites |
| `/seguir` | Vuelve a operar |
| `/desarmar` | Apaga el dinero real: no abre nada nuevo, lo abierto sale normal |
| `/cerrar SI` | Emergencia: cierra ya todo lo real y lo apaga |
| `/ayuda` | La lista de órdenes |

Armar dinero real **no** se puede por Telegram: solo en la PC, con la frase exacta. Una orden que llegó
mientras el programa estaba apagado no se ejecuta después.

## Parada de emergencia y verificación

- **Pausa (KILL):** creá un archivo vacío `%LOCALAPPDATA%\TRADING\KILL`. Con ese archivo no se abren
  posiciones. Para reanudar, borralo.
- **Diario:** `.\.venv\Scripts\python -m tradingagent journal-verify` dice si alguien lo alteró.
- **Estado:** el tablero (acceso directo **TRADING tablero**) o `%LOCALAPPDATA%\TRADING\state\heartbeat.json`. Logs en
  `%LOCALAPPDATA%\TRADING\logs`.

## Entrenamiento en la nube (GitHub Actions, sin la PC)

Un workflow junta los datos con los que aprende el agente (`.github/workflows/recoleccion.yml`). Es el
respaldo de la PC: aunque la PC esté apagada, los datos se siguen juntando y el paper por corridas se
sigue midiendo. GitHub a veces demora el cron (el 2-nov tardó horas en dar la primera corrida
programada). En cada corrida:

1. Lee las fuentes y guarda las noticias nuevas de hasta 3 horas en la rama `data`. Las copias de otras
   fuentes y lo que parece ruido también quedan, marcados. Cada noticia lleva su clasificación (`nlp`).
   Acá Claude no se consulta (la noticia llega minutos tarde y ya no cambiaría nada): solo con `--ia`.
2. Una hora después de cada noticia, mide la reacción del precio con velas públicas de 1 minuto
   (Binance; si no responde, Coinbase) a 1, 5, 15 y 60 minutos desde la publicación, y la triple barrera
   desde la llegada. Con eso aprende: apaga lo que pierde y ajusta el meta-modelo (solo reduce riesgo).
3. Opera en **paper**, cada 5 minutos, en dos cuentas simuladas de 120 USDT: Binance spot (solo largos)
   y futuros de MEXC (largos y cortos a 1x). Todo queda en `paper/<mercado>/` de la rama `data`. Con el
   cron, la noticia llega minutos tarde: casi siempre el alfa ya se fue y no entra. Es lo esperable: la
   evidencia en serio sale de la PC.
4. Por Telegram, en castellano simple:
   - un aviso al activarse;
   - las noticias oficiales de la Reserva Federal y la SEC;
   - un resumen de noticias por día, con una línea por cuenta de práctica de respaldo.

   Las operaciones de práctica de GitHub **no** se avisan una por una (son un respaldo y confundían con las
   de la PC): las que te llegan son las de tu PC.

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
