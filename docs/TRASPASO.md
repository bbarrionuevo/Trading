# TRASPASO: qué es TRADING, en qué estado está y cómo seguir

Para el agente que toma el sistema, sobre todo si trabaja **en la PC del dueño con dinero real en
producción**. Leelo entero antes de tocar nada. Al día del 3-nov-2026 (noche), versión `main` después del PR #23.

## 1. Qué es y qué quiere el dueño

**TRADING** es un agente de trading por noticias, en Python (asyncio). Funciona así:
- lee noticias y anuncios oficiales en vivo;
- decide qué activo mueve cada noticia, para qué lado y con qué fuerza;
- opera en **futuros perpetuos de MEXC** criptos, acciones, índices, oro y petróleo;
- sale con barreras medidas con datos;
- avisa todo por Telegram en castellano simple.

Corre 24/7 en la PC Windows del dueño.

**El dueño** (Brian) no es programador. Hay que hablarle en castellano rioplatense, breve, claro y sin jerga.
Lo que pidió, en sus palabras:

- "debe ser 100 % autonomo"; "tenés autonomía completa".
- "Futuros solo con MEXC" (Binance tiene límites para Argentina).
- "elimina los topes, siempre se va a trabajar e invertir con todo el dinero disponible, iniciaremos con 100usdt
  pero en caso de ganar e incrementar se podrá trabajar e invertir todo (según lo que ya sabemos y arrojó la
  investigación y nuestra experiencia)".
- "operar con distintas monedas, cripto, acciones o lo que sea necesario según la noticia".
- IA: "que la use siempre y solamente que sea necesario".
- "reaccionar lo más rápido posible a las noticias".
- "voy a necesitar que estés conectado prácticamente de forma sincrónica con mi PC para vigilar cómo está
  actuando el programa y poder realizar modificaciones rápidas de ser necesario".

## 2. Estado al 3-nov-2026 (noche)

| Qué | Estado |
|---|---|
| PC | Al día. El supervisor la actualiza sola desde `main` con el CI en verde. Tests en Windows OK |
| Cuenta de práctica (paper) | 120 USDT simulados en futuros MEXC; 3 operaciones el 3-nov (−0,20 USDT, cortos mal leídos por las reglas antes de que la IA confirmara cada entrada) |
| Simulacro del real | Armado el 3-nov con 100 USDT de mentira, 2 días. Mismo operador real, sin mandar órdenes |
| **Dinero real** | **Arranca el 4-nov con 100 USDT** en la cuenta de futuros de MEXC. El dueño corre `mexc-real-check` y después `armar-real` (frase `ARMO DINERO REAL CON TODO EL SALDO POR 30 DIAS`). Al armar el real, se reemplaza el simulacro. Leído el 3-nov a las 20 h (solo lectura): la cuenta ya está en modo Cobertura, sin posiciones ni órdenes, y **el saldo de futuros es 0**: el dueño pasa los 100 USDT a Futuros el 4-nov antes de la prueba |
| Aprendizaje | **Arreglado el 3-nov (PR #23).** El meta-modelo había quedado en κ = 0 con 47 muestras, 37 de ellas noticias ajenas con BTC "de referencia" (Ferrari, 3M, "heladeras hackeadas"): p = 1/3 en todo y ninguna entrada posible, listados incluidos, desde la tarde del 3-nov. Ahora solo aprende de lo que se habría operado (sección 9) |
| Fuentes | 14: Fed; medios cripto; CNBC, Seeking Alpha, Investing; anuncios oficiales de Binance (10 s) y Upbit (10 s, PR #22). OKX y Bitget apagados (sin efecto medido). SEC apagada (falta `SEC_USER_AGENT`) |
| IA | Claude Haiku 5.5. Lee toda noticia que las reglas abrirían. Unos 0,002 USD por día. Techo de seguridad: 5 USD por día |
| Tareas programadas (nube) | "Revisión diaria de TRADING" 7:36 y "Vigilancia de TRADING cada 2 horas" 8 a 24 (hora argentina). Sin conector de GitHub: dejan arreglos en ramas `claude/...` |

## 3. Dónde está cada cosa en la PC

| Qué | Dónde |
|---|---|
| Código (lo maneja el supervisor) | `%USERPROFILE%\TRADING`, con el entorno en `.venv` |
| Datos privados (`TRADING_HOME`) | `%LOCALAPPDATA%\TRADING` |
| Claves | `%LOCALAPPDATA%\TRADING\.env`: `MEXC_ACCESS_KEY`, `MEXC_SECRET_KEY` (futuros, sin retiro), `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GITHUB_TOKEN_PC`. **Nunca mostrarlas ni copiarlas** |
| Armado del real o del simulacro | `ARMADO.json` (lo crea solo el dueño) |
| Pausa | `KILL` (archivo vacío: no abre nada nuevo) |
| Estado vivo | `state\heartbeat.json` (latido cada 15 s), `state\supervisor.json`, `state\verificacion_real.json`, `state\telegram-offset.json` |
| Registros | `logs\agent.log` (JSON por línea), `logs\agente-salida.log`, `logs\supervisor.log`, `logs\tests.txt`, `logs\red.json` |
| Cuentas | `paper-vivo\mexc-futuros\`, `simulacro\mexc-futuros\` (y su `exchange-simulado.json`), `real\mexc-futuros\` |
| Cada cuenta tiene | `estado.json`, `operaciones\AAAA-MM.jsonl`, `decisiones\AAAA-MM.jsonl` |
| Otros | `datos\universo-mexc.json`, `datos\aprendizaje\`, `datos\estudios\`; diario firmado en `journal\` |
| Tarea de Windows | `trading-agente` (corre `python -m tradingagent supervisar` y la relanza cada minuto si se cae) |
| Tablero local | http://127.0.0.1:18765 o el siguiente puerto libre (el 8765 lo usa otro programa del dueño). Acceso directo **TRADING tablero** |
| Telegram | bot @StarkLedger_bot, solo para el chat del dueño |

**Muy importante:** el supervisor actualiza el código con `git checkout -f -B main <sha>`. **Todo cambio local en
`%USERPROFILE%\TRADING` se pierde** en la próxima actualización y puede romperla. Para programar, usá un clon
aparte (sección 7).

**Nunca toques nexus:** `%USERPROFILE%\nexus`, `%LOCALAPPDATA%\nexus` y las tareas `nexus-*` son otro proyecto
del dueño.

## 4. Cómo funciona (de la noticia a la orden)

1. **Fuentes** (`ingestion/catalog.py`, `sources.py`, `announcements.py`). RSS y APIs JSON, leídas cada 10 a 60
   s con ETag, backoff y salud por fuente. Upbit verifica el certificado con el almacén de Windows
   (`SourceSpec.os_trust`, paquete `truststore`).
2. **Ingesta** (`ingestion/engine.py`, `dedup.py`, `noise.py`). Frescura (`news.max_age_s` del sobre),
   deduplicado por similitud (una sola operación por historia) y ruido.
3. **Clasificación rápida** (`intelligence/classifier.py`, versión `reglas-3`). Reglas en inglés y coreano:
   hackeo, deslistado, quiebra, regulatorio, ETF, macro, listado, resultados, fusión, etc. Activos en
   `research/assets.py` más el universo de MEXC.
   - Si la noticia no nombra un activo, BTC de referencia **solo** si es un hecho de mercado (macro, regulatorio,
     ETF, tesorería, quiebra). El hecho de una empresa o de algo fuera del universo queda sin activo: nada que
     operar ni consultar.
   - Los resultados de empresas nunca son de una cripto ("LTC reports Q3 earnings" es LTC Properties).
   - Un ticker sin letras (la moneda "4") no se detecta en el texto: era "$4.2B".
4. **Universo** (`marketdata/universe.py`). Todo MEXC futuros operable por API con volumen de 24 h ≥ 0,1 M USDT
   (~350 activos). Los líquidos (≥ 1 M) se reconocen por nombre o ticker; los chicos, solo por su ticker escrito
   ("(FOO)", "$FOO"). Caché de 6 h.
5. **IA** (`intelligence/llm.py`, Claude Haiku 5.5). Se consulta si las reglas abrirían algo
   (`LiveTrader.rules_would_enter`) o si el hecho es dudoso. Da categoría, dirección, activos y relevancia,
   importancia e impacto (0 a 10). El vector toma el menor entre eso y el a priori: la IA solo puede achicar.
   **Sin la lectura de la IA no se entra**; con dinero real, sin IA nunca (REGLAS C-4).
6. **Decisión** (`decision/policy.py`, `intelligence/vector.py`, `meta.py`).
   - α = P·C·I·M con vida media de 120 s; meta-modelo p ≥ 0,60.
   - Triple barrera: stop 1,5σ√H con piso 0,6 % y tope 5 %, objetivo 2 veces el stop.
   - ¼-Kelly sobre el riesgo; costos: comisión, spread, impacto de raíz cuadrada y funding en contra.
   - Bloqueos de microestructura: spread, profundidad, L3, divergencia, Kyle y base contra el precio de marca
     mayor a 1 %.
   - "No correr detrás del precio" (`chase_blocks`).
7. **Perfiles por categoría** (`policy.EXITS`, medidos con datos):
   - **listado:** solo con menos de 2 min; stop de 5 % que **sigue al mejor precio**, sin objetivo fijo; tope de
     precio de 50 bps; exento de "ya recorrió el objetivo" (`DRIFT`).
   - **macro:** solo con menos de 90 s.
8. **Riesgo** (`decision/risk.py`, `config/limits.yaml`).
   - 1 % del saldo por operación al stop; 40 % por posición; 80 % bruto; 30 % neto; 3 posiciones; 1x.
   - Frenos: 2 % por día y 4 % por semana; drawdown de 8 % = HALT. Tres pérdidas seguidas: 60 min de pausa.
9. **Ejecución.**
   - Práctica: `execution/paper.py` contra el libro real.
   - Real: `execution/live/venue.py`.
     - Órdenes IOC con tope de precio, margen aislado y stop de emergencia en el exchange (1,5 veces el stop).
     - Margen contra el saldo disponible y conciliación con MEXC cada vuelta.
     - Si MEXC ya cerró una posición, se sincroniza y avisa.
   - Simulacro: `execution/live/dryrun.py`.
   - El cliente firmado de MEXC es `mexc_futures.py`, con lista cerrada de endpoints y sin retiros.
10. **Vigilancia de posiciones** (`live.py`). Cada segundo, contra el libro: stop, stop móvil (se guarda),
    objetivo y vencimiento. Funding. Resumen diario a las 21:00 hora argentina.
11. **Todo el saldo** (`LiveTrader.sync_capital`). Sin posiciones abiertas, la caja del real es el saldo de MEXC.
    Depósitos y retiros corren los frenos: no cuentan como ganancia o pérdida.
12. **Avisos y órdenes** (`alerts/`).
    - Telegram en castellano simple.
    - Órdenes del dueño: `/estado`, `/pausa`, `/seguir`, `/desarmar`, `/cerrar SI`. Nunca armar.
13. **Supervisor** (`supervisor.py`).
    - Relanza al agente y se actualiza solo con el CI de `main` en verde y sin posiciones abiertas (espera hasta
      6 h). Si la versión nueva no arranca, vuelve atrás.
    - Corre los tests en la PC.
    - Publica la rama pública `pc`: cada 2 min si hay algo armado o posiciones, si no cada 15. Publica
      práctica, simulacro, salud, errores, `tests.txt` y `red.json`, **nunca la cuenta real** (G-8).
14. **Aprendizaje** (`research/learn.py`, `collector.py`). Cada 15 min etiqueta noticias con su reacción. Solo
    reduce riesgo: apaga "categoría:lado" con evidencia en contra y ajusta el meta-modelo. **Solo cuenta lo que
    se habría operado** (`learn.entry_alpha`): relevancia C ≥ 7 y |α| ≥ 0,10 al entrar, los mismos umbrales de
    la decisión. Si cambia la versión del clasificador, recalcula todo en la primera pasada (al arrancar).

## 5. Comandos (en la PC)

Siempre así: `cd $env:USERPROFILE\TRADING; .\.venv\Scripts\python -m tradingagent <comando>`

| Comando | Para qué |
|---|---|
| `mexc-real-check` | Prueba sin riesgo de la clave real: órdenes que no se pueden ejecutar, verificación y cancelación. Hace falta antes de armar |
| `armar-real` | Arma el dinero real con todo el saldo. **Solo lo corre el dueño:** pide la frase exacta (REGLAS A-2) |
| `armar-real --simulacro --capital 100 --dias 2` | El mismo operador real sin mandar órdenes |
| `armar-real --desarmar` | No abre nada nuevo; lo abierto sale normal |
| `cerrar-todo` | Emergencia: cancela y cierra todo lo real en MEXC, y desarma |
| `sources` / `sources --check` | Qué fuentes están activas / leer cada una una vez |
| `mexc-check` | Datos públicos de MEXC y si las claves leen la cuenta |
| `estudio-listados` | Repite el estudio de listados de Binance (`datos\estudios\listados.json`) |
| `journal-verify` | Verifica que el diario firmado no se tocó |
| `tablero` | El tablero local |

Pausa sin comandos: crear `%LOCALAPPDATA%\TRADING\KILL`.

## 6. Vigilar con dinero real

Qué mirar, en orden:

1. **Latido:** `state\heartbeat.json`, campo `ts`, con menos de 1 minuto. Si es viejo, el agente no corre: mirá
   `logs\supervisor.log` y la tarea `trading-agente`.
2. **Cuenta real:** `real\mexc-futuros\estado.json` (caja, posiciones, `risk`), `operaciones\` y
   `decisiones\` del mes.
3. **Errores:** `logs\agent.log`, líneas con `"WARNING"` o `"ERROR"`. Señales de problema con plata real:
   - "orden real ... rechazada";
   - "conciliacion" o "diferencias" (posiciones de MEXC distintas de las del programa);
   - "exchange_sin_respuesta";
   - "ya estaba cerrada en el exchange";
   - "sin_saldo_leido";
   - "supera_el_saldo_disponible".
4. **Por qué no entra:** los `motivos` de las decisiones. Todo frenado por un mismo motivo raro puede ser un bug.
   Ejemplo real (3-nov): `meta_p_baja:0.33` en todo era el aprendizaje con κ = 0
   (`datos\aprendizaje\estado.json`, `meta.global.kappa`).
5. **MEXC:** si hay dudas, compará las posiciones del programa con las de MEXC (`mexc-check` o la web de MEXC).

**Emergencias, de menos a más:**
1. `/pausa` por Telegram o el archivo `KILL`.
2. `armar-real --desarmar`.
3. `cerrar-todo` (o `/cerrar SI`).

Salir siempre se puede. Si algo raro está perdiendo plata, primero frená y después investigá. Avisale al
dueño qué hiciste y por qué.

## 7. Cómo cambiar el código

1. **Nunca en `%USERPROFILE%\TRADING`.** Cloná aparte, por ejemplo `gh repo clone bbarrionuevo/Trading
   $env:USERPROFILE\TRADING-dev`. El dueño tiene `gh`.
2. Rama `claude/<tema>` desde `main`. `pip install -e ".[dev]"`, `ruff check src tests tools` y `python -m pytest`
   (≈ 230 tests, unos 2 minutos).
3. PR a `main`. El CI corre en Linux (3.11, 3.12, 3.13) y en Windows. En un PR protegido, el job
   `protected-paths` falla a propósito: marca que lo aprueba el dueño.
4. Sin rutas protegidas y con el CI en verde, el agente lo puede fusionar (REGLAS G-3). Las protegidas las fusiona
   el dueño (G-2): `.github/`, `docs/REGLAS.md`, `config/limits.yaml`, `src/tradingagent/limits.py`, `safety.py`,
   `paths.py`, `execution/live/` y `tests/test_rules.py`, `tools/`.
5. **La PC se actualiza sola:** con algo armado busca cada 5 min, si no cada 15. Si hay posiciones abiertas
   espera a que cierren, hasta 6 h. El supervisor reinstala dependencias si cambia `pyproject.toml`.
6. Commits en castellano. Fin del mensaje: `Co-Authored-By: Claude …` y `Claude-Session: …`. Sin el
   identificador del modelo en commits, PR ni código.

## 8. Reglas que no se negocian (resumen de `docs/REGLAS.md`)

- **A-1:** no aflojar el sobre (`config/limits.yaml`, línea base en `limits.py`) sin el OK explícito del dueño. El
  sistema de permisos frena intentos de aflojarlo, y está bien.
- **A-2:** el dinero real solo lo arma el dueño, en la PC, con la frase exacta. Nunca lo armes vos.
- **A-3:** órdenes solo desde `execution/live/mexc_futures.py` (lista cerrada). Nunca retiros ni transferencias.
- **C-1 a C-4:** la IA es un dato no confiable (esquema, rangos, el activo tiene que aparecer en el texto). La IA
  propone y el motor de riesgo decide. Sin la lectura de la IA no se entra.
- **G-1:** ningún secreto en el repo, los logs, los avisos ni la rama `pc`. No imprimas el `.env`.
- **G-8:** la rama `pc` es pública: nunca saldos ni montos de la cuenta real.
- Acciones de GitHub fijadas por SHA. Nunca tocar nexus. No gastar la API de Claude de más.

## 9. Evidencia (por qué los parámetros son como son)

- **Las reglas solas leen mal** (3-nov): "Mercado Bitcoin" (un exchange de Brasil) se tomó como BTC, y "los IOU
  de Liquid vuelven a estar respaldados" como mala noticia. Por eso la IA lee toda entrada.
- **Listados spot de Binance** (`docs/ESTUDIO-LISTADOS.md`, n = 7, 6 meses):
  - mediana +12,6 % a 5 min, +28 % a 60 min;
  - con stop que sigue al precio a 5 %: +12,7 % por operación (contra +4,9 % con objetivo fijo);
  - **entrando a los 5 minutos, todo pierde**: llegar primero es todo.
- **Lanzamientos de futuros de Binance** (n = 9): saltan y se devuelven. No se operan.
- **OKX** (n = 4): sin efecto. Bitget: casi todo acciones. Apagados.
- **Fed** (`docs/ESTUDIO-MACRO.md`, n = 4): reacción en los primeros minutos y después va para cualquier lado en BTC
  y los índices. Macro solo con menos de 90 s.
- **Spread y profundidad** de las monedas chicas en MEXC: 0,4 a 6 bps y miles de USDT. No limitan órdenes de
  20 a 30 USDT.
- **Comisiones:** futuros 0 a 0,02 % por lado contra 0,05 % de spot. A 1x, el futuro es lo mismo que comprar y
  permite cortos. Spot no se usa: solo 6 monedas líquidas no tienen futuro.
- **MEXC guarda velas** de 1 min ~30 días, de 5 min ~6 meses y de 60 min más de 1 año.
- **El aprendizaje con muestras ajenas frena todo** (3-nov, datos de la PC): de 47 muestras del meta-modelo, 37
  eran BTC "de referencia" de noticias que no lo nombran y 46 tenían |α| < 0,10 (nunca se iban a operar). Con la
  expiración de 60 min casi nunca tocan el take-profit: 0 de 47, κ = 0, p = 1/3 en todo. Recalculado con el
  código nuevo sobre los mismos datos: 1 muestra válida, κ = 1 (por defecto) y un listado fresco con α = 0,40 da
  p = 0,74 (con el aprendizaje roto daba 0,33). 53 de 421 noticias guardadas cambian de activo (46 eran BTC).
- **El operador real recibe la misma lectura de la IA que la práctica**: las 45 decisiones del simulacro del
  3-nov coinciden una por una con las de la práctica (la caché de la IA comparte la consulta).

## 10. Pendiente (en orden)

1. **4-nov, dinero real.** Acompañar el armado del dueño: primero pasa los 100 USDT a Futuros (el 3-nov el saldo
   de futuros era 0; el modo Cobertura ya está). `mexc-real-check` tiene que dar todo OK. Después vigilar las
   primeras operaciones reales: llenado, deslizamiento, stop de emergencia en MEXC (la prueba no manda
   `stopLossPrice`: confirmarlo en la primera entrada real, en la web de MEXC o leyendo la orden), conciliación y
   avisos. Antes de armar, confirmar que el aprendizaje recalculado dejó `meta` sin κ = 0.
2. **Upbit** (PR #22). La fuente `upbit.avisos` lee bien con el almacén de Windows (3-nov: 20 avisos de historia,
   sin fallas). Falta ver que un listado en wones ("신규 거래지원") se reconozca y opere.
3. **Bybit:** desde la PC sus avisos andan (`red.json`). Estudiar si sus listados mueven el precio antes de
   sumarlos (como OKX, quizá no). El estudio tiene que correr en la PC: desde la nube Bybit está bloqueado.
4. **Proteger la ganancia del stop móvil en el exchange** (si se apaga la PC, hoy el stop de emergencia queda en
   el lugar inicial). Necesita sumar un endpoint de órdenes de stop a la lista de A-3: es ruta protegida, lo
   aprueba el dueño.
5. **Dato de inflación (CPI):** la BLS da 403 desde la PC y desde la nube. Buscar otra fuente rápida.
6. **Tareas programadas con conector de GitHub:** para que abran y fusionen PRs solas, el dueño se lo agrega en
   claude.ai → Rutinas.
7. **Más evidencia:** que el aprendizaje junte operaciones reales y de práctica (F-3: 60 operaciones con DSR ≥
   0,95 da una medida seria). Revisar el meta-modelo con ellas.
8. **El aprendizaje mide la lectura de las reglas, no la de la IA**, que es la que decide cada entrada (las reglas
   leen mal: "Mercado Bitcoin", "Roundup settlement" de Bayer como regulatorio cripto). Guardar con cada noticia
   la lectura de la IA cuando la hubo y aprender con esa.
9. **La etiqueta del meta-modelo** (triple barrera del etiquetador) expira a los 60 min, y el prior supone un
   paseo sin vencimiento (1/3 con α = 0). Con entradas reales, revisar si hay que calibrar el prior con la
   expiración. Hoy no frena: solo cuentan las apuestas que se habrían hecho.

## 11. Detalles que muerden (Windows y MEXC)

- **Windows:**
  - `os.replace` falla si otro proceso tiene el archivo abierto (`paths.write_json_atomic` reintenta);
  - la consola cp1252 rompía con emojis (se fuerza UTF-8);
  - el error `WinError 10054` de asyncio es inofensivo y va a DEBUG;
  - un puerto de 127.0.0.1 se puede pisar sobre 0.0.0.0 (el tablero prueba conectando antes);
  - un job de Windows mata al agente si muere el supervisor.
- **MEXC futuros:**
  - la cuenta tiene que estar en modo **Cobertura (Hedge)**;
  - lados 1 abrir largo, 2 cerrar corto, 3 abrir corto, 4 cerrar largo; tipo 3 = IOC; `openType` 1 = aislado;
  - bandas de precio de ±10 %;
  - las acciones se llaman distinto (TSLA → `TESLA_USDT`, AAPL → `AAPLSTOCK_USDT`): usá el universo;
  - la API de trading por API abrió el 31-mar-2026 para cuentas con KYC.
- **Binance:** la API (`api.binance.com`) anda desde Argentina y no desde la nube; los anuncios
  (`www.binance.com/bapi`) andan desde los dos.
- **Upbit:** solo con `truststore` (la cadena del certificado viene incompleta).
