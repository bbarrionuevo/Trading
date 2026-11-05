# TRASPASO: qué es TRADING, en qué estado está y cómo seguir

Para el agente que toma el sistema, sobre todo si trabaja **en la PC del dueño con dinero real en
producción**. Leelo entero antes de tocar nada. Al día del 4-nov-2026, versión `main` después del PR #26.

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

## 2. Estado al 4-nov-2026

| Qué | Estado |
|---|---|
| PC | Al día. El supervisor la actualiza sola desde `main` con el CI en verde. Tests en Windows OK |
| Cuenta de práctica (paper) | **Pasa a futuros de Binance (4-nov)**, con 120 USDT simulados nuevos (`paper-vivo/binance-futuros/`): criptos y **TradFi** (acciones, ETF, oro, petróleo), con los precios públicos de Binance. La de MEXC queda guardada en `paper-vivo/mexc-futuros/` (3 operaciones el 3-nov, −0,20 USDT); se vuelve a ella con `trader.mercado: mexc-futuros` en config.yaml |
| Simulacro del real | **Simulacro LOCAL sobre producción de Binance** (pedido del dueño, 4-nov; PR protegido): `armar-real --simulacro`. Es el operador real completo (misma noticia, decisión, riesgo y venue) con el libro y la comisión REALES de Binance, pero cada orden se ejecuta en la PC (`execution/live/binance_dryrun.py`): nivel por nivel contra el libro del momento, con su tope de precio, y el stop de emergencia simulado con el precio de marca. **Capital real 100 % aislado:** no recibe la clave de trading; la de solo lectura lee saldo y comisión y Binance la rechaza para operar (-2015, probado el 4-nov). Arranca con **100 USDT de mentira, nunca con el saldo real**: el simulacro se publica en la rama pública `pc` (el 4-nov se armó con el saldo real y quedó publicado unos minutos: se rearmó con 100). Registro: `simulacro/binance-futuros/ordenes-simuladas.jsonl`; informe: `informe-simulacro`. Probado contra producción el 4-nov (SOL y Tesla). `--demo` lo hace contra la demo de Binance. El de MEXC del 3-nov ya no corre |
| **Dinero real** | **No arranca hasta que el dueño lo confirme** (4-nov: "debemos estar seguros de que todo funciona"). Va por fases: primero la prueba sin riesgo y el simulacro en Binance; después poca plata con vigilancia constante y actuar de inmediato ante cualquier falla; la última fase, cuando esté comprobado que todo funciona y gana. El armado lo hace siempre el dueño (`armar-real` con la frase). Historia: el 4-nov la prueba de MEXC falló primero por la dirección vieja (403) y después por la restricción de la cuenta (fila siguiente) |
| **MEXC** | **Descartado (4-nov).** Con la dirección nueva, la prueba llegó a MEXC y respondió código 6002 (la cuenta no puede abrir posiciones), y el dueño informó una restricción permanente de la cuenta. El dueño decidió pasar la ejecución a **Binance Futures USDT-M**: la práctica ya opera con los precios de Binance; el simulacro sigue con los de MEXC hasta que esté el de Binance (ruta protegida, lo fusiona el dueño) |
| **Binance Futures** | **Nuevo lugar de ejecución (4-nov).** Paso 1 (fusionado, PR #29, #32 y #35): cliente `execution/live/binance_futures.py` con lista cerrada, stop de emergencia por el servicio Algo y prueba sin riesgo `binance-real-check` (contrato XRPUSDT). **Prueba en la DEMO de Binance (plata ficticia, claves `BINANCE_API_KEY_TEST` / `BINANCE_API_SECRET_TEST`, `https://demo-fapi.binance.com`), 4-nov: los 10 pasos OK** (el stop se vio cancelado a los 0,6 s). En la cuenta real la corre el dueño (pone órdenes reales). **Hay 235 perpetuos TradFi** además de los 703 de cripto: ~200 acciones de EE. UU., Corea y Hong Kong, SPY y QQQ, oro, plata, petróleo, gas y cobre, **las 24 h, también el fin de semana**; 170 y 449 con más de 1 M USDT por día. La práctica ya opera con los precios de Binance (PR #36). **Paso 2 (PR protegido, lo fusiona el dueño):** operador real `execution/live/binance_venue.py` (IOC con tope, aislado 1x en cada contrato, stop de emergencia Algo de cierre total al llenarse la entrada, conciliación que lo repone y frena si algo no coincide), armado por exchange, simulacro en la DEMO y `cerrar-todo` para Binance. **Probado de punta a punta en la demo el 4-nov:** SOL largo y XRP corto entraron, pusieron su stop al precio pedido, conciliaron, salieron y no quedó nada abierto (costo: spread y comisión, un centavo). Tesla primero rechazada (falta el acuerdo TradFi, código -4411); **el dueño lo aceptó y Tesla anduvo de punta a punta en la demo** (0,05 TSLA, stop a −15 %, salida, nada abierto). Antes de operar: cuenta en modo unidireccional y de un solo activo |
| **Estrategias (5-nov)** | Cada una con su estudio, su stop y su tope de tamaño (`policy.EXITS`): **listados** de Upbit y Binance (largo, con IA, 0,3 del saldo); **resultados de empresas con impulso** (0,6; sin IA, lo decide el precio a los 2 min); **perpetuos nuevos que ya bajaban** (corto, 0,065; sin IA); **deslistados de Binance** (corto, 0,10; sin IA, si el precio todavía no cayó); **etiqueta de vigilancia de Binance** (corto, 0,10 con stop de 10 %: 1 %; sin IA, si no cayó más de 2 %). El resto de las noticias pasa por la IA y el meta-modelo (0,3). Medidas y no operadas: tendencia (falta capital), funding extremo y momentum entre activos (sin ventaja) |
| Aprendizaje | **Arreglado el 3-nov (PR #23).** El meta-modelo había quedado en κ = 0 con 47 muestras, 37 de ellas noticias ajenas con BTC "de referencia" (Ferrari, 3M, "heladeras hackeadas"): p = 1/3 en todo y ninguna entrada posible, listados incluidos, desde la tarde del 3-nov. Ahora solo aprende de lo que se habría operado (sección 9) |
| Fuentes | 15: Fed; medios cripto; CNBC, Seeking Alpha, Investing; anuncios oficiales de Binance (cada 3 s, rotando 72 formas de la URL para saltar la copia de ~120 s del CDN) y Upbit (cada 8 s: su límite es ~10 pedidos por minuto). OKX y Bybit apagados o sin sumar (sin efecto medido), Bitget apagado. SEC prendida desde el 4-nov (el dueño cargó `SEC_USER_AGENT`): los 8-K llegaban con ~72 s de demora mediana (392 del 4-nov, leídos cada 60 s); ahora se leen cada 20 s y 100 por página (en el pico de los resultados entran ~56 por hora con el ítem 2.02). **PR Newswire, canal de resultados**, cada 30 s (su CDN guarda 60 s). Business Wire guarda 5 min en su CDN y GlobeNewswire y Accesswire bloquean a los programas: no se usan (no se fuerza un bloqueo). **Nuevo (PR protegido): el canal oficial de anuncios de Binance por WebSocket** (`ingestion/binance_ws.py`), que empuja cada anuncio de todos los catálogos cuando se publica (sin sondeo ni copia del CDN). Usa una clave de Binance de SOLO LECTURA aparte (`BINANCE_LECTURA_KEY` / `BINANCE_LECTURA_SECRET`): la crea el dueño; sin ella queda apagado y el sondeo de listados sigue igual. Probado desde la PC el 4-nov: conecta en ~1,2 s. **Demora medida el 5-nov (5 avisos): 1,2 a 1,8 s de publicado a recibido.** PR Newswire (resultados) llega con mediana de 143 s: más lento que la SEC (72 s leyendo cada 60 s). **Los medios llegan tarde para operar** (demora mediana de publicada a recibida, 4-nov: The Block 31 s, CoinDesk 56 s, Seeking Alpha 173 s, Investing 424 s, CNBC 592 s): solo sirven de contexto y para salir; lo que se opera son los avisos oficiales |
| Velocidad (4-nov) | De recibida a decisión: 0,4 s sin IA y 3,8 s con IA (mediana). Las demoras largas (hasta 14 s) eran datos de mercado bajados para noticias que igual se descartaban, con el candado tomado: arreglado en el PR #26. Lo que más tarda ahora es la consulta a la IA (~3,4 s) |
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
3. **Clasificación rápida** (`intelligence/classifier.py`, versión `reglas-4`). Reglas en inglés y coreano:
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
   - **listado:** solo con menos de 2 min; **stop FIJO de 10 % y salida a los 15 min**, sin objetivo (A-5, aprobado
     por el dueño el 4-nov: calibración con Upbit en Binance; el stop que seguía al precio a 5 % perdía); tope de
     precio de 50 bps; exento de "ya recorrió el objetivo" (`DRIFT`).
   - **macro:** solo con menos de 90 s.
   - **Cada estrategia se juzga sola** (F-3, `LiveTrader.refresh_verdicts`, cada 10 min): con 30 o más operaciones cerradas de una categoría y DSR < 0,80, deja de abrir esa categoría (`categoria_descartada_por_resultados`) y avisa una vez por Telegram. Vale también para las que no pasan por la IA.
   - **Segunda lectura de la IA** (deslistado y vigilancia): se entra sin esperarla; después la IA lee el aviso y, si dice que no es negativo para esa moneda, se cierra (`la_ia_no_confirma`).
   - **vigilancia_corto** (`decision/delistings.py`, mismo camino): aviso "Binance Will Extend the Monitoring Tag to …": corto si el precio todavía no cayó más de 2 %; **stop FIJO de 10 %, salida a las 4 h**, arriesgando 1 %. A los 3 s casi todo ya cayó: el filtro deja afuera las veces que se llega tarde.
   - **deslistado_corto** (`decision/delistings.py`): apenas llega por el canal oficial un aviso "Binance Will
     Delist ...", corto en el perpetuo de cada moneda si el precio todavía no cayó más de 4 % desde el minuto del
     aviso; **stop FIJO de 20 % y salida a las 4 h**, arriesgando 2 % (0,10 veces el saldo). Sin IA (C-4 y A-5,
     excepción medida, `docs/ESTUDIO-DESLISTADOS.md`); la regla general no vuelve a entrar con ese aviso.
   - **lanzamiento_corto** (`decision/launches.py`): el operador mira el catálogo de Binance cada minuto; a los 15
     min del inicio de un perpetuo de cripto nuevo, si el precio quedó en o debajo del primero, corto con **stop
     FIJO de 30 % y salida a las 72 h**, arriesgando 2 % (0,065 veces el saldo). Sin IA (C-4 y A-5, excepción
     medida, `docs/ESTUDIO-LANZAMIENTOS.md`). Con pocas velas, σ se mide con 10 retornos (solo para costos).
   - **resultados_impulso** (`decision/earnings.py`): solo entre los minutos 2 y 4 del 8-K de resultados y si el
     perpetuo TradFi ya se movió ≥ 0,5 % en esa dirección; **stop FIJO de 5 % y salida a los 30 min**, sin objetivo;
     exento de "ya recorrió el objetivo" (`DRIFT`). Sin IA: la dirección la da el precio (C-4 y A-5, excepción
     medida, `docs/ESTUDIO-RESULTADOS.md`).
8. **Riesgo** (`decision/risk.py`, `config/limits.yaml`).
   - **3 % del saldo por operación al stop** (el dueño, 4-nov; antes 1 %); 40 % por posición; 80 % bruto; 30 % neto;
     3 posiciones; 1x. Con el stop de 10 % de los listados, la posición es ~30 % del saldo (el tope neto).
   - **Tope por estrategia** (`ExitProfile.max_x`, veces el saldo, `docs/ESTUDIO-APALANCAMIENTO.md`): resultados
     0,6 (con su stop de 5 % arriesga el 3 %); listados 0,3; sin estudio propio 0,3. El sobre manda por encima: con
     el de antes (neta 30 %) quedaban en 0,3. **Sobre del PR `claude/operar-mas`** (protegido): posición ≤ 60 %,
     bruto ≤ 100 %, neto ≤ 60 %, 4 posiciones, **1x**; el riesgo por operación y los frenos no cambian. **El
     apalancamiento es consecuencia de un stop cercano, nunca un objetivo**; hoy ninguna estrategia lo necesita y
     con 2x la liquidación quedaría demasiado cerca del stop de 30 % de los lanzamientos.
   - Con 3 %, un stop completo pasa el freno del día (no abre hasta la noche) y tres pérdidas seguidas llegan al
     halt del 8 % (vuelve con rearme del dueño). Simulación de la calibración: mes típico +8 %, mes malo −3 %,
     caída máxima típica en un año ~10 % (`docs/ESTUDIO-LISTADOS.md`).
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
| `binance-real-check` | Prueba sin riesgo de la clave real de Binance: una orden que no se puede ejecutar y un stop lejísimos, verificación y cancelación. Hace falta antes de armar. **La corre el dueño** (pone órdenes reales) |
| `binance-real-check --demo` | La misma prueba en la DEMO de Binance (plata ficticia, claves `*_TEST`). No cuenta para armar. La puede correr Claude |
| `armar-real` | Arma el dinero real en Binance con todo el saldo. **Solo lo corre el dueño:** pide la frase exacta (REGLAS A-2). `--exchange mexc` para MEXC |
| `armar-real --simulacro --dias 7` | El simulacro local: el operador real con el libro real de Binance y órdenes simuladas en la PC, con 100 USDT de mentira (nunca el saldo real: se publica). `--capital N` para otro capital, `--demo` contra la demo de Binance, `--exchange mexc` en MEXC |
| `informe-simulacro` | Calibración: llegada de las noticias por fuente, de la noticia a la entrada, acierto, ganancia neta por categoría, deslizamiento. `--cuenta practica|demo|real`, `--dias N` |
| `armar-real --desarmar` | No abre nada nuevo; lo abierto sale normal |
| `cerrar-todo` | Emergencia: cancela órdenes y stops y cierra todo lo real en Binance (con tope de precio), y desarma. `--demo` en la demo; `--exchange mexc` en MEXC |
| `mexc-real-check` | La prueba sin riesgo de MEXC (cuenta restringida desde el 4-nov) |
| `sources` / `sources --check` | Qué fuentes están activas / leer cada una una vez |
| `mexc-check` | Datos públicos de MEXC y si las claves leen la cuenta |
| `estudio-listados` | Repite el estudio de listados de Binance (`datos\estudios\listados.json`); con `--exchange bybit`, el de Bybit (`listados-bybit.json`, solo desde la PC) |
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
- **Deslistados de Binance** (4-nov, `docs/ESTUDIO-DESLISTADOS.md`, segundo a segundo con el archivo público de
  Binance): el precio cae entre los 2 y 5 s del aviso (mediana −8 % a los 5 s). Corto a los 2 s, stop 20 %, 4 h:
  mediana +18 %, media +13 %, 79 % positivas (34 monedas de 17 avisos desde 2025); a los 5 s sin filtro +2,6 % (2025:
  −3 %). Si todavía no cayó más de 4 %, +7 a +12 % de media aunque se llegue a los 3-5 s.
- **Resultados y el control de liquidez** (5-nov, las 60 operaciones del estudio): el freno de Kyle (> 4 veces el impacto de precio de la hora previa) deja afuera 14 (23 %). Esas dieron mediana +0,34 % y 8 de 14 positivas; las que entran, mediana +1,41 % y 31 de 46. El freno se queda: saca las más flojas.
- **6-K de empresas extranjeras** (5-nov, `estudios/eventos_6k.py`, 473 avisos de 15 empresas con perpetuo: TSMC, Alibaba, SK Hynix, Novo Nordisk, Sony, ARM, ASML...): solo 4 % mueve el perpetuo a los 2 min (al azar 2,5 %) y seguirlo no rinde (n 12, media +0,25 %). Publican los resultados en su país antes del 6-K: cuando llega a la SEC, el mercado ya reaccionó. No se opera. **Coinbase:** su página de estado solo trae incidentes técnicos, no listados (no sirve de fuente).
- **Otros 8-K de la SEC** (5-nov, `estudios/eventos_sec.py`, 1.136 8-K sin resultados de 141 empresas con perpetuo desde febrero): mueven el precio (impulso ≥ 0,5 % a los 2 min en 9 % de los casos, contra 1,5 % en horas al azar), pero seguirlo no rinde (n = 44, media +0,6 %, 52 % positivas; por ítem, muestras de 8 a 23). No se opera.
- **Etiqueta de vigilancia de Binance** (5-nov, `docs/ESTUDIO-DESLISTADOS.md`, 31 monedas de 14 avisos desde 2025): cae −0,7 % a los 2 s y −4 % a los 5 s; corto a los 2 s si no cayó más de 2 %, stop 10 %, 4 h: mediana +3,6 %, 85 % positivas (2025 flojo, 2026 18 de 18). A los 3-5 s ya no hay ventaja.
- **Upbit, avisos negativos, segundo a segundo en Binance** (5-nov, `estudios/upbit_negativos.py`): advertencias (n 13) sin ventaja; fin del soporte (n 6) cae de a poco en el primer minuto (−3 % a los 10 s, −6 % al minuto) y el corto a los 10 s con 4 h dio +4 %, −4 %, +1 %, +17 %, +19 % y −11 %: seguir midiendo.
- **Camino interno del agente** (5-nov): de un aviso a la orden, ~30 ms (antes ~220 ms: el lector de libros dejaba 200 ms entre pedidos; ahora balde de fichas, 5 por segundo con ráfaga).
- **Funding extremo** (4-nov, `estudios/funding.py`, 114 perpetuos 2020-2026): sin ventaja estable (se da vuelta según
  el período y unos pocos saltos enormes mandan en el promedio). No se opera.
- **Perpetuos nuevos** (4-nov, `docs/ESTUDIO-LANZAMIENTOS.md`, 521 desde 2023 con funding y costos): largo
  pierde siempre; corto a todos rinde poco (+1,1 % de media a las 72 h con stop de 30 %); **los que a los 15 min ya
  bajaban: n = 250, mediana +7,4 %, media +4,2 %, 62 % positivas** (2023 −0,6 %, 2024 +3,1 %, 2025 +7,5 %, 2026
  +2,4 %); los que subieron más de 30 % siguen subiendo.
- **Tamaño** (4-nov, `docs/ESTUDIO-APALANCAMIENTO.md`, las 60 operaciones de resultados): 0,3 veces el saldo da
  mediana +32 % al año (remuestreado); 0,6 (el 3 % aprobado) +73 % con 9 % de años con caída ≥ 8 %; 1x o más pasa
  el freno de 8 % casi siempre y arriesga 5 a 15 % por operación. Kelly da 11 a 15 veces: no se usa (muestra chica).
- **Tendencia** (4-nov, `docs/ESTUDIO-TENDENCIA.md`, 114 perpetuos incluidos los cerrados, 2020-2026, con costos y
  funding): 20 a 60 días, diversificada en todo lo líquido y a volatilidad de 20 %, gana en 2020-2022 y en 2023+
  (30 días: +32 % y +16 % por año, caída −11 % y −24 %); concentrada en 3 a 10 contratos pierde o casi no gana;
  "momentum entre activos" no funciona. Necesita ~1.000 a 2.000 USDT (35 a 90 posiciones sobre el mínimo de
  Binance) y un presupuesto de caída propio (sus caídas pasan el 8 %). Los estudios se repiten con `estudios/`.
- **Resultados de empresas en los TradFi de Binance** (4-nov, `docs/ESTUDIO-RESULTADOS.md`, 88 resultados de 36
  empresas): el perpetuo reacciona despacio (~16 % del movimiento de la hora en el primer minuto). Entrando a los 2
  minutos detrás del movimiento (≥ 0,5 %), stop de 5 % y salida a los 30 min: mediana +1,01 %, media +1,17 % neto de
  costos, 39 de 60 positivas. Entrando al minuto 1 o al 5, sin ventaja.
- **OKX** (n = 4): sin efecto. Bitget: casi todo acciones. Apagados.
- **Upbit** (4-nov, desde la PC, `docs/ESTUDIO-LISTADOS.md`): alta en wones +20,9 % a 5 min (14 de 14), listado nuevo
  +13,5 % (21 de 21), fin del soporte −7,4 % (6 de 6), advertencia −2,6 %. Casi todo dentro del primer minuto:
  entrando al minuto 1 la mediana cae a +1,1 %. Es la señal más fuerte y la más frecuente (~1,4 por semana).
- **Para no ser baneados en Binance** (4-nov, reglas cuantitativas de USDⓈ-M y límites de la API):
  - las reglas cuentan por contrato y por ciclos de 10 min, y se miden desde 5.000 a 10.000 órdenes por ciclo:
    órdenes sin ejecutar, IOC vencidas, cancelaciones antes de 5 s y órdenes de menos de 50 USD;
  - el programa hace pocas órdenes por día y queda lejos, pero igual nunca cancela antes de 5 s;
  - ante un 429 hay que frenar: insistir lleva a un 418, que banea la IP de 2 minutos a 3 días;
  - operar solo desde la PC (nunca desde la nube ni con VPN);
  - `/fapi/v1/apiTradingStatus` dice si hay un bloqueo.
- **Calibración con avisos reales** (4-nov): sin datos, la IA calificaba un listado de Upbit con importancia 3 e
  impacto 4. Como el vector toma el menor entre la IA y el a priori, el sistema no entraba en ninguno (NIL +24 %,
  CT +29 %, KAIA +34 % en el primer minuto o la hora). Con la reacción medida de cada tipo de aviso oficial en el
  prompt (`llm.SYSTEM`), entra en los 3 listados (p = 0,77) y en el fin del soporte de BLUR (corto, p = 0,69), y
  no entra en las advertencias (±3 a 6 %). La IA sigue leyendo cada noticia y el vector sigue tomando el menor.
- **Upbit en Binance Futures, segundo a segundo** (4-nov, `docs/ESTUDIO-LISTADOS.md`): +23 % a los 5 s del aviso.
  Entrando a los 10-20 s, saliendo a los 15 min con stop de 10 %: mediana +0,6 % a +3,0 %, media +4,3 % a +6,6 %
  neto (n = 23). Con la salida de antes (stop que sigue al precio a 5 %): mediana −0,2 % a −1,0 %. Aplicado (A-5,
  aprobado por el dueño el 4-nov).
- **Bybit** (3-nov, desde la PC): listados spot +0,4 % a 5 min (n = 4) y perpetuos +0,03 % (n = 48). Sin efecto: no
  es fuente.
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

1. **Simulacro local en Binance** (armado el 4-nov por 7 días, 100 USDT de mentira): vigilarlo y calibrar con
   `informe-simulacro` (acierto, ganancia por categoría, de la noticia a la entrada, deslizamiento). Hecho antes: el
   cliente, la prueba sin riesgo (10 OK en la demo), el operador real probado en la demo (SOL, XRP y Tesla), el
   acuerdo TradFi aceptado, la clave de solo lectura y el canal de anuncios. Con evidencia del simulacro, el dueño
   corre `binance-real-check` en la cuenta real y decide si arma el dinero real (`armar-real`), por fases.
2. **Upbit** (la señal más fuerte; se lee cada 8 s por su límite de ~10 pedidos por minuto, así que un aviso llega
   en promedio ~4 s después de publicado más la red). Con el próximo aviso, revisar en el simulacro la demora de
   punta a punta y el precio de entrada contra el del aviso. Probar un segundo camino a los avisos sin pasar el
   límite, midiendo con cuidado (un exceso bloquea la IP de la PC).
2b. **IA:** medido el 4-nov, ~1,9 s por consulta (esfuerzo bajo; sin razonamiento da lo mismo: Haiku 5.5 ya no
   razona con esfuerzo bajo), ~5.200 tokens de entrada y ~130 de salida. **No es el cuello de botella** (lo son las
   fuentes). Si hiciera falta: caché del sistema (mínimo cacheable de Haiku 5.5: 512 tokens; dura 5 min, o 1 h
   pagando el doble de escritura) y un "reason" más corto (~0,2 s).
3. **Resultados de empresas con impulso** (PR protegido del 4-nov, `docs/ESTUDIO-RESULTADOS.md`): a los 2 min del
   8-K de resultados, si el perpetuo TradFi ya se movió ≥ 0,5 %, se entra en esa dirección (stop fijo de 5 %,
   salida a los 30 min). Estudio de 88 resultados: mediana +1,01 %, media +1,17 % neto, 39 de 60 positivas. La SEC
   se lee cada 20 s y PR Newswire (resultados) cada 30 s. Revisar con las operaciones del simulacro y la práctica.
4. **Listados de Coinbase:** la PC llega a su API de productos (prueba de red); estudiar si un listado nuevo mueve
   el precio en Binance antes de sumarlo. Bithumb bloquea la PC (403): no se puede.
5. **Capital chico:** en Binance BTC pide al menos 50 USDT por orden (ETH 20, lo demás 5). Con ~100 USDT y 3 % de
   riesgo, muchas entradas de BTC no entran; las altcoins y los TradFi, sí.
6. **Proteger la ganancia del stop móvil en el exchange** (si se apaga la PC, el stop de emergencia queda donde se
   puso). En Binance se puede reemplazar el stop Algo: ruta protegida, lo aprueba el dueño.
7. **Dato de inflación (CPI):** la BLS da 403 desde la PC y desde la nube. Buscar otra fuente rápida.
8. **Más evidencia:** que el aprendizaje junte operaciones reales y de práctica (F-3: 60 operaciones con DSR ≥
   0,95 da una medida seria). Revisar el meta-modelo con ellas.
9. **El aprendizaje mide la lectura de las reglas, no la de la IA**, que es la que decide cada entrada. Guardar con
   cada noticia la lectura de la IA cuando la hubo y aprender con esa.
10. **La etiqueta del meta-modelo** (triple barrera del etiquetador) expira a los 60 min, y el prior supone un
    paseo sin vencimiento (1/3 con α = 0). Con entradas reales, revisar si hay que calibrar el prior.
11. **Tareas programadas:** las de la nube no ven la PC ni tienen conector de GitHub. Hecho el 4-nov: la vigilancia
    del simulacro corre cada 3 h (8 a 23 h) desde la app de la PC.
12. **Tendencia diversificada** (`docs/ESTUDIO-TENDENCIA.md`): es la estrategia de los fondos CTA y gana fuera de
    muestra, pero necesita ~1.000 a 2.000 USDT y un presupuesto de caída propio (sus caídas pasan el 8 %). Cuando el
    capital lo permita, programarla con ajuste semanal y su propio freno (lo decide el dueño).
13. **Órdenes maker** (0,02 % en vez de 0,05 % de comisión) para lo que no tiene apuro (salidas por tiempo).
14. **Más velocidad, con costo (lo decide el dueño):** titulares de resultados con "supera / no alcanza lo esperado"
    en segundos (Benzinga por Alpaca: cuenta propia del dueño) y un servidor cerca de Binance (Tokio) en vez de la PC.

## 11. Detalles que muerden (Windows y MEXC)

- **SEC lenta, no caída (5-nov):** la página de 8-K tarda de 0,4 a 8 s y a veces más de 10 s (64 veces en 14 h). Con la espera que se duplica tras cada falla, 4 fallas seguidas dejaban ~10 min sin la SEC. Ahora: hasta 25 s de espera, reintento como mucho al minuto y el corte de 10 min recién después de 20 fallas.
- **El sitio de anuncios de Binance (www.binance.com) cuenta los pedidos por IP:** el agente lo lee cada 3 s. Un estudio que lo consulte desde la PC provoca 429 en el agente: el 4-nov a 2 s entre pedidos y el 5-nov **también a 6 s** (tres 429 seguidos cada vez; se recuperó en ~20 s). Desde la PC: un pedido cada 30 s como mínimo y cortar al primer 429 (`estudios/vigilancia_binance.py` lo hace). Los datos de mercado de fapi.binance.com tienen su propio límite (2.400 de peso por minuto, también compartido con el agente: los estudios frenan a 900).

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
  - la API de trading por API abrió el 31-mar-2026 para cuentas con KYC;
  - **la API de futuros está en `https://api.mexc.com` desde el 19-ene-2026** y la orden va a
    `POST /api/v1/private/order/create` (documentación oficial). `contract.mexc.com` sigue contestando lecturas
    pero bloquea en la entrada (Akamai, "Access Denied", HTTP 403 en HTML) todo envío y cancelación de órdenes,
    con o sin clave: así falló la primera prueba real el 4-nov. Un 403 en HTML es un bloqueo de la entrada, no de
    la clave; un error de la clave llega en JSON.
- **Binance:** la API (`api.binance.com`) anda desde Argentina y no desde la nube; los anuncios
  (`www.binance.com/bapi`) andan desde los dos.
- **Binance Futures:**
  - desde el 2-dic-2025 las órdenes condicionales (STOP_MARKET, TAKE_PROFIT...) van por el **servicio de órdenes
    Algo** (`/fapi/v1/algoOrder`); por la ruta común dan el código -4120 (visto en la PC el 4-nov). No tienen
    ruta de prueba: `binance-real-check` pone uno que solo se dispararía con +50 % y lo cancela a los 6 s;
  - **el stop de cierre total (`closePosition`) lo vence Binance solo** cuando la posición se cierra (EXPIRED, en
    milisegundos, demo 4-nov): cancelarlo después contesta -2011 "Unknown order" y no hay nada que hacer;
  - Binance arranca cada contrato en 20x: el operador lo pone en aislado y 1x antes de la primera entrada;
  - **los TradFi piden aceptar su acuerdo** en cada cuenta (demo y real): sin eso, código -4411 "Please sign
    TradFi-Perps agreement". El programa avisa por Telegram una vez por día;
  - **la cancelación de un stop Algo se ve con demora:** el DELETE contesta "success", pero la consulta enseguida
    (~0,5 s después) todavía no decía CANCELED (4-nov, en la PC; minutos después sí, y no había quedado nada
    abierto). Hay que volver a consultar unos segundos antes de darlo por fallido: la prueba
    consulta hasta 10 s y guarda en `state/verificacion_binance.json` los estados vistos y cuánto tardó. Desde la
    PC, cada ida y vuelta a Binance tarda ~0,3 s (a veces 0,9 s);
  - `/fapi/v1/order/test` valida órdenes comunes sin mandarlas;
  - `aggTrades` históricos solo de los últimos 2 días (código -4166): para estudiar segundos, las velas de 1 s
    del spot (`/api/v3/klines?interval=1s`) tienen historia;
  - **TradFi** (`contractType` = `TRADIFI_PERPETUAL`, 235 el 4-nov): acciones, ETF, oro (`XAUUSDT`, funding cada
    4 h), plata (`XAGUSDT`), petróleo (`CLUSDT` WTI, `BZUSDT` Brent), gas, cobre, platino y paladio, las 24 h.
    Quedan fuera del universo las empresas que todavía no cotizan (`PREMARKET`: OpenAI, Anthropic) y los índices de
    cripto (`BTCDOMUSDT`). La noticia dice SILVER, USOIL, UKOIL, SP500 y NAS100: son alias de XAG, CL, BZ, SPY y QQQ;
  - monedas de precio muy chico con multiplicador: `1000PEPEUSDT`, `1MBABYDOGEUSDT` (la noticia dice PEPE);
  - **nominal mínimo por orden: 50 USDT en BTC**, 20 en ETH y 5 en casi todo lo demás. Con ~100 USDT y 3 % de
    riesgo, un corto de BTC de 36 USDT no entra;
  - **demo** (plata ficticia): `https://demo-fapi.binance.com` (documentación oficial; `testnet.binancefuture.com`
    es la misma). Los precios siguen a los reales pero el libro es más ancho y artificial (BTC con ~10 bps de
    spread, Tesla ~30): sirve para la mecánica (órdenes, ejecuciones, stops, conciliación), no para medir
    deslizamiento ni ganancia.
- **Upbit:** solo con `truststore` (la cadena del certificado viene incompleta).

## 12. Registro de estudios y pruebas (para el que siga)

**El plan de acción vigente está en `docs/PLAN.md`** (sección del 5-nov: lo que sirve de los dos informes de
investigación del dueño y de sus cuatro ideas, lo próximo en orden, lo que necesita su decisión y lo descartado).
Cada estudio se puede repetir con su script de `estudios/` (datos públicos; ojo con los límites por IP de la
sección 11). Un estudio nuevo va con su línea acá.

| Fecha | Qué se probó | Resultado | Decisión | Dónde |
|---|---|---|---|---|
| 3-nov | Listados spot de Binance (n 7) | +12,6 % a 5 min; entrando a los 5 min pierde | se opera (`listado`) | ESTUDIO-LISTADOS |
| 3-nov | FOMC (n 4) | después de los 5 min, moneda al aire | macro solo con < 90 s | ESTUDIO-MACRO |
| 4-nov | Upbit en MEXC (velas de 5 min) | listado +13,5 %; fin del soporte −7,4 % (n 6); advertencia −2,6 % | listados se operan | ESTUDIO-LISTADOS |
| 4-nov | Resultados 8-K 2.02 en TradFi (88) | a los 2 min, si se movió ≥ 0,5 %: media +1,17 %, 39 de 60 | se opera (`resultados_impulso`) | ESTUDIO-RESULTADOS |
| 4-nov | Tamaño y apalancamiento (60 ops) | 0,6 veces = el 3 % aprobado; ≥ 1x salta el freno de 8 % | tope por estrategia, 1x | ESTUDIO-APALANCAMIENTO |
| 4-nov | Tendencia, 114 perpetuos 2020-26 | diversificada gana fuera de muestra; concentrada no | no (falta capital) | ESTUDIO-TENDENCIA |
| 4-nov | Momentum entre activos | sin ventaja | no | ESTUDIO-TENDENCIA |
| 4-nov | Perpetuos nuevos (521) | corto si a los 15 min ya bajó: media +4,2 % | se opera (`lanzamiento_corto`) | ESTUDIO-LANZAMIENTOS |
| 4-nov | Funding extremo | sin ventaja estable | no | `estudios/funding.py` |
| 4-nov | Deslistados de Binance (34, al segundo) | a los 2 s media +12,8 %; si no cayó 4 %, +7 a +12 % | se opera (`deslistado_corto`) | ESTUDIO-DESLISTADOS |
| 5-nov | Otros 8-K de la SEC (1.136) | mueven el precio, seguirlo no rinde | no | `estudios/eventos_sec.py` |
| 5-nov | 6-K de extranjeras (473) | sin ventaja (resultados publicados antes en su país) | no | `estudios/eventos_6k.py` |
| 5-nov | Etiqueta de vigilancia de Binance (31) | a los 2 s, si no cayó 2 %: media +3,6 %, 85 % | se opera (`vigilancia_corto`) | ESTUDIO-DESLISTADOS |
| 5-nov | Freno de liquidez (Kyle) en resultados | saca 23 %, las más flojas | se queda | sección 9 |
| 5-nov | Precio de marca en resultados | frena 1 de 60 | se queda | sección 9 |
| 5-nov | Upbit, advertencias y fin del soporte (al segundo) | advertencias sin ventaja (n 13); fin n 6, media ~+4 % | seguir midiendo | `estudios/upbit_negativos.py` |
| 5-nov | Coinbase como fuente | su página de estado no publica listados | no | — |
| 5-nov | Canal de anuncios de Binance | de publicado a recibido 1,2-1,8 s (5 avisos) | medido | sección 2 |
| 5-nov | Camino interno del agente | de un aviso a la orden: 220 → 30 ms (balde de fichas) | hecho (PR #49) | sección 9 |
| 5-nov | Control de peso de la API de Binance | lee `X-MBX-USED-WEIGHT-1M`; > 80 %: lo no urgente espera | hecho | `marketdata/peso.py` |
| 5-nov | Segunda lectura de la IA para las reglas sin IA | si no confirma el aviso, cierra la posición | hecho | `live.py` |
| 5-nov | **1.ª operación del simulacro** | Wendy's, resultados: entró a los 120 s, salió a los 30 min, −0,27 USDT (−0,5 %); su comunicado salió minutos antes del 8-K | normal (pierden ~35 %) | simulacro |
