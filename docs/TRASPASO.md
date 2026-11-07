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

## 2. Estado al 4-nov-2026 (y lo nuevo del 5-nov arriba)

**7-nov, a la tarde: instalados v3.8 y la fase 2 de fuentes; v3.9 de Upbit esperando al dueño.** En Tokio desde las 15:21 UTC: la etiqueta de vigilancia con freno de 4 % y tope de 2 % (simulacro), el relé leyendo también la lista sin catálogo (respaldo del WebSocket para deslistados, vigilancia y HODLer) y el aviso de catálogo nuevo. El cruce de los listados de Upbit con la hora REAL de Telegram (§12) cambió la foto: el alta en wones es la que gana (+13,8 % por aviso entrando a 0,7 s del mensaje) y el listado nuevo depende de décimas (pasado 1 s, la operación típica pierde). REGLAS v3.9 (PR protegido): freno de 25 % y el listado nuevo solo hasta 1 s del mensaje. El relé anota la demora real de cada aviso de Upbit (`~/.trading/datos/telegram-upbit.jsonl`) desde que se instale.

**7-nov: avisos coreanos al instante y los deslistados listos para dinero real** (REGLAS v3.7,
`decision/korean_notices.py`; pedidos del dueño: "Sí, armalas" y "a Deslistados preparemos todo y hagamos las pruebas q
tengamos q hacer y pasmos a dinero real. Tope de la orden: tenes autorizacion para ensanchar. si fin de soporte o corto
por advertencia upbit es bueno preparemos todo tambien para ya entrar en real"). Dos reglas nuevas: la **advertencia de
Upbit** (corto, hasta 30 s del mensaje, si no cayó 3 %; stop 10 %, 4 h, 2 % al stop) y el **fin del soporte** de Upbit o
Bithumb, el primer aviso que llega (corto, hasta 5 s, si no cayó 6 %; stop 10 %, 5 min, 1 % al stop). Bithumb entra como
fuente (`bithumb.avisos`, la lista de avisos de su web, cada 1 s; solo alimenta esa regla). El **deslistado de Binance**
pasa a tope de orden de 2 % (el sobre: techo de 200 pb; lo demás sigue ≤ 0,5 %) y, como las caídas al instante, no mira
las alarmas del libro (B-4; quedan anotadas en `alarmas_del_libro`). Probado: la prueba de punta a punta en la PC (con
el lector de Bithumb de verdad contra un Bithumb falso), una ida y vuelta de un corto de verdad en la DEMO de Binance con
el venue del dinero real y `binance-real-check --demo` con el paso nuevo del corto. **Instalado en Tokio el 7-nov a
las 09:13 UTC** (#80, `953bb9b`), sin posiciones reales abiertas: las tres corren en el simulacro; el dinero real sigue
con los dos listados hasta que el dueño las sume (sección 6). Prueba de punta a punta allá, con el Binance real: OK.

**6-nov a la tarde: listados de Binance al instante** (REGLAS v3.6, `decision/binance_listings.py`): apenas llega "Binance Will List …" o un HODLer Airdrop de una moneda con perpetuo, si la orden sale a ≤ 2,2 s de publicado y el precio no subió más de 8 %, largo sin esperar a la IA (stop 10 %, 15 min, 3 % al stop, como Upbit). El dueño pidió armarla ya con dinero real ("siendo tan pocos los avisos [...] quizá sea bueno armarlo ya para dinero real"): la arma él sumándola al armado (sección 6). Hasta entonces corre en el simulacro de Tokio.

**5-nov a la noche: TODO corre en el servidor de Tokio** (pedido del dueño: "queda ya todo en Tokio"):

- **Tokio** corre el agente completo (`agente-tokio`) y el relé (`rele-tokio`). Es el simulacro de Binance con 100 USDT
  de mentira, armado el 5-nov, más la práctica (120 USDT simulados). Lee todas las fuentes y estrategias, y opera los
  listados de Upbit al instante por su canal oficial de Telegram. El bot de Telegram (alertas, /pausa, /estado) está en
  Tokio.
- **La PC está en "solo tablero"** (`%LOCALAPPDATA%\TRADING\SOLO-TABLERO`). El supervisor no corre al agente; sigue
  actualizándose, sirve el tablero y publica la rama `pc`. Late al relé (la pausa de la PC frena a Tokio) y trae por SSH
  el tablero completo de Tokio, que se ve como si el agente corriera en la PC. El simulacro de la PC quedó desarmado
  (3 operaciones, +1,00 USDT de mentira: se ve en "Cuentas que ya no corren").
- **DINERO REAL ARMADO desde el 6-nov a las 13:11 UTC, solo para los listados de Upbit al instante** (lo armó el
  dueño en Tokio: `armar-real --estrategias listado_upbit`, todo el saldo de futuros, vence el 6-dic). Verificado
  solo leyendo: el ejecutor puede abrir, `listado_upbit` pasa y las demás estrategias (y una orden sin estrategia)
  no; leyó el saldo y el 30 % pasa el mínimo de 5 USDT; sin pausa ni errores. El simulacro de 100 USDT dejó de
  correr (el armado es uno solo); lo demás sigue en la práctica.
- **Sin práctica, con el simulacro junto al dinero real** (REGLAS v3.5, pedido del dueño del 6-nov: "la practica tiene
  sentido? creo que es mejor los simulacros"; "opción 1 siempre que los simulacros de los demás módulos no compitan
  o afecten en nada a los módulos que trabajen con dinero real"): en Tokio corren el dinero real (solo Upbit) y un
  simulacro aparte (`SIMULACRO.json`, 100 USDT de mentira) de los demás módulos, en el mismo agente. El simulacro
  nunca opera lo que opera la plata; el dinero real tiene sus propias conexiones con Binance y no lee las noticias
  que no puede operar; la práctica está apagada (`trader.practica: false`). **Instalado el 6-nov a las 16:28 UTC**
  (#73): `SIMULACRO.json` armado por 30 días, el armado real intacto (solo `listado_upbit`), sin errores. Antes, el
  6-nov:
  Armado por estrategia (REGLAS v3.4, #67, fusionado por el dueño). El 6-nov a las 12:28 UTC se instaló en Tokio con
  #66 (el bucle y la memoria), #68 (la prueba en la PC) y #69 (conexiones tibias con Binance). La prueba de punta a
  punta en Tokio dio: listados de Upbit con la orden a los 50-57 ms la primera y 21-29 ms las demás, el deslistado a
  los 24 ms, la IA confirmando en 1,1-1,7 s y sin errores. Lo demás sigue en práctica.
- **Reglas:** v3.4 (#67): armado por estrategia. v3.3 (#58): 3 % en los listados de Upbit, nada se cancela en Binance
  antes de 5 s, lineamientos medidos (sección L de REGLAS).
- **Servidor nuevo desde el 6-nov** (el viejo se quedó sin red el 5-nov; sección 11): ~900 MB, misma IP. Tiene
  memoria acotada por servicio, aviso por Telegram si se calla y el vigía de la red, que instaló el dueño. La
  instancia vieja ya no está.

| Qué | Estado |
|---|---|
| PC | Al día. El supervisor la actualiza sola desde `main` con el CI en verde. Tests en Windows OK. Desde el 5-nov a la noche, solo tablero |
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
| Tablero local | http://127.0.0.1:18765 o el siguiente puerto libre (el 8765 lo usa otro programa del dueño). Acceso directo **TRADING tablero**. Desde el 7-nov (pedido del dueño: "que todo se vea en la misma ventana sin scrolear [...] el dinero real y cómo varía [...] cuadrados que muestren cada módulo [...] si es dinero real o simulacro"), "/" es la **pantalla simple**: el saldo real de la cuenta de futuros y su curva, lo ganado, las últimas 24 h, las operaciones y lo abierto, y un rectángulo por módulo con su ganancia, operaciones y la etiqueta DINERO REAL o SIMULACRO; entra en 1366×768 sin scroll y se recarga cada 30 s (antes, todo cada 5 s). El detalle de siempre quedó en `/detalle` (cada 15 s) |
| Telegram | bot @StarkLedger_bot, solo para el chat del dueño |

**Muy importante:** el supervisor actualiza el código con `git checkout -f -B main <sha>`. **Todo cambio local en
`%USERPROFILE%\TRADING` se pierde** en la próxima actualización y puede romperla. Para programar, usá un clon
aparte (sección 7).

**Nunca toques nexus:** `%USERPROFILE%\nexus`, `%LOCALAPPDATA%\nexus` y las tareas `nexus-*` son otro proyecto
del dueño.

### Servidor de Tokio: cómo conectarse y qué hay (desde el 5-nov, `docs/RELE-TOKIO.md`)

- **Conectarse:** `ssh -i C:\Users\Brian\.ssh\lightsail-tokio.pem -o BatchMode=yes ubuntu@<IP>`. La IP está en
  `host` de `%LOCALAPPDATA%\TRADING\relay.json`; nunca se escribe en el repo, que es público. La llave la dio el
  dueño, y la PC ya conoce al servidor (`known_hosts`; huella ED25519 desde el 6-nov:
  `SHA256:enk3HfrP3BHU2xGLZZHFExFRCxXAgEVsIfbBFYPrp1g`). **Usar el ssh de Git**
  (`C:\Program Files\Git\usr\bin\ssh.exe`, o Git Bash): el de Windows rechaza la llave por sus permisos ("bad
  permissions"). Ubuntu 22.04, 2 vCPU, **~900 MB de memoria** (desde el 6-nov) **y 1 GB de swap** (`/swapfile`, desde el 6-nov), 20 GB
  de disco. Las actualizaciones automáticas de Ubuntu corren a las 15:00 UTC (±10 min).
- **Servicios** (systemd, se reinician solos):
  - `rele-tokio`: el canal oficial de Upbit en Telegram y la lista de Binance; reparte por UDP 50005.
  - `agente-tokio`: `python -m tradingagent run` con `TRADING_HOME=/home/ubuntu/.trading`.
  - `vigia-red.timer`: el vigía de la red, cada minuto (sección 11). Su registro: `sudo journalctl -t vigia-red`.
  - Ver: `systemctl is-active rele-tokio agente-tokio vigia-red.timer`.
  - Logs: `sudo journalctl -u agente-tokio --since -1h --no-pager -p warning` (y `-u rele-tokio`).
- **Carpetas:**
  - `/home/ubuntu/trading`: el repo, en `main`, con `.venv` de Python 3.11.
  - `/home/ubuntu/.trading`: TRADING_HOME del agente (estado, diario, simulacro, `config.yaml`, `relay.json` local,
    `relay.key`).
  - `/home/ubuntu/relay.key` y `/home/ubuntu/telegram/` (sesión de Telegram y canal fijado).
- **Claves** (nunca se muestran; todas con permisos 600):
  - `/home/ubuntu/.env` es del dueño: `TG_API_ID`, `TG_API_HASH`, `API_Key_Binance`, `SECRET_Key_Binance` (la de
    trading: IP restringida, sin retiros) y `API_IA`. El relé lee de ahí solo las de Telegram.
  - `/home/ubuntu/.trading/.env` es la del agente, armada con `deploy/tokio/preparar_env.py`: la IA, la de solo lectura
    de Binance, `SEC_USER_AGENT` y el bot de Telegram. **Sin la clave de trading** hasta que el dueño lo pida.
  - Mandar las de la PC: `python deploy/tokio/enviar_claves_pc.py --telegram`, desde el worktree.
- **Actualizar los dos servicios**, con el CI de `main` en verde:
  `ssh -i C:\Users\Brian\.ssh\lightsail-tokio.pem ubuntu@<IP> "bash ~/trading/deploy/tokio/instalar-agente.sh"`.
  Corre pip solo si cambiaron las dependencias, y con los servicios parados.
- **Memoria:** el agente usa ~180-220 MB (6-nov; el SDK de la IA solo, ~45 MB) y el relé ~50-60 MB, y quedan ~330 MB
  disponibles más 1 GB de swap. Por partes: `bash ~/trading/deploy/tokio/memoria.sh`. Los servicios tienen tope
  (`MemoryHigh` / `MemoryMax`: agente 400 / 500 MB, relé 150 / 200 MB) y `OOMScoreAdjust=-500`. No correr procesos
  pesados en el servidor. El 5-nov, con 416 MB, la falta de memoria dejó al servidor sin red (sección 11).
- **Telegram de Upbit:** la sesión es de un número secundario del dueño. Si se cae (`STATUS` del relé: desconectado),
  el inicio de sesión lo hace el dueño (pide el código que le llega al teléfono):
  `cd ~/trading && .venv/bin/python -m tradingagent.relay.telegram_upbit --login`.

### Relé de Tokio (desde el 5-nov, `docs/RELE-TOKIO.md`)

- Servidor AWS Lightsail en Tokio (ap-northeast-1), usuario `ubuntu`, llave `C:\Users\Brian\.ssh\lightsail-tokio.pem`. Código en `/home/ubuntu/trading` (el repo), servicio `rele-tokio` (systemd, se reinicia solo), clave del relé en `/home/ubuntu/relay.key`.
- En la PC: `TRADING_HOME/relay.json` (dirección y puerto UDP) y `TRADING_HOME/relay.key` (la misma clave; nunca se muestra). El agente manda un latido firmado cada 10 s y recibe los avisos.
- Ver cómo anda: `ssh -i C:\Users\Brian\.ssh\lightsail-tokio.pem ubuntu@<IP> "sudo journalctl -u rele-tokio -n 50"`. Actualizar: `cd ~/trading && git pull && sudo systemctl restart rele-tokio` (o `bash deploy/rele-tokio/instalar.sh`).
- Desde el 5-nov también corre ahí el **agente completo en simulacro** (servicio `agente-tokio`, `TRADING_HOME=/home/ubuntu/.trading`, fase 2 de `docs/RELE-TOKIO.md`): todas las fuentes y estrategias, con Binance a 2-5 ms. Su `.env` tiene la IA, la clave de SOLO LECTURA y el `SEC_USER_AGENT`; **no** la de trading (esa está en `/home/ubuntu/.env`, del dueño, y no se usa hasta la fase 3, con REGLAS aprobadas).
- Su estado llega al tablero de la PC (sección "Servidor de Tokio") por telemetría firmada a través del relé; la pausa de la PC (KILL o /pausa) se copia sola al agente de Tokio.
- **Desde el 5-nov a la noche, todo corre en Tokio** (pedido del dueño): la PC está en "solo tablero" (archivo `%LOCALAPPDATA%\TRADING\SOLO-TABLERO`): el supervisor no corre al agente, sirve el tablero, publica y late al relé (`relay/monitor.py`). El bot de Telegram está en Tokio. Volver atrás: borrar `SOLO-TABLERO` (y sacar `TELEGRAM_*` del `.env` del agente de Tokio, `docs/RELE-TOKIO.md`).
- Actualizar los dos servicios (con el CI de `main` en verde): `ssh -i C:\Users\Brian\.ssh\lightsail-tokio.pem ubuntu@<IP> "bash ~/trading/deploy/tokio/instalar-agente.sh"`. Logs: `sudo journalctl -u agente-tokio -n 50` y `-u rele-tokio`.

### Instalar, parar, arrancar y reiniciar Tokio (procedimiento, 7-nov)

Pedido del dueño: "anotate algún procedimiento de instalación para que siempre revises las conexiones tibias y etc antes
de instalar" y "si algún día tengo que parar el programa o reiniciar la pc de tokio me gustaría que todo arranque solo y
perfecto". La revisión es `bash ~/trading/deploy/tokio/revisar.sh` (solo lee; cada línea dice OK, OJO o FALLA): los
tres servicios andando y habilitados para arrancar solos, el código contra `main`, la memoria, el armado y cuándo vence,
la prueba sin riesgo, las posiciones abiertas, el latido y las fuentes, el canal de anuncios de Binance, el bucle, **las
conexiones tibias con Binance** (4 por grupo: 8 con el dinero real y el simulacro), Telegram del relé, la charla con la
PC, los errores de los últimos 15 min y la pausa.

**Instalar** (siempre en este orden):
1. En la PC: el CI de `main` en verde en Linux y Windows (`gh run list --branch main --limit 1`).
2. En Tokio: `bash ~/trading/deploy/tokio/revisar.sh --antes`. Con posiciones REALES abiertas da FALLA: no se instala
   hasta que cierren (G-7; el instalador también se niega).
3. `bash ~/trading/deploy/tokio/instalar-agente.sh`: instala, reinicia los dos servicios y a los 30 s corre la revisión
   sola. Todo tiene que dar OK (el "relé habla con la PC" puede tardar 5 min en aparecer).
4. La prueba de punta a punta: en la PC siempre (`prueba_listado.py --pc`); en Tokio cuando cambia el servidor, Python o
   las dependencias, o antes de armar dinero real.
5. Anotarlo en §12 (versión, hora, la revisión y la prueba).

**Parar, arrancar y reiniciar** (por SSH en el servidor):
- **Pausa** (no abre nada nuevo; lo abierto sale normal): /pausa por Telegram o el KILL de la PC (se copia en ~1 s);
  en el servidor, `touch ~/.trading/KILL` (se saca con `rm ~/.trading/KILL`).
- **Parar el programa**: `sudo systemctl stop agente-tokio rele-tokio`. Las posiciones abiertas quedan solo con su stop
  de emergencia en Binance (1,5 veces el stop): sin el agente no hay salida por tiempo ni segunda lectura de la IA.
- **Arrancar**: `sudo systemctl start rele-tokio agente-tokio` y después `revisar.sh`.
- **Reiniciar el servidor**: `sudo reboot`. En 1 a 2 minutos todo arranca solo: los servicios están habilitados,
  esperan a la red y, si se caen, systemd los relanza cada 5-10 s sin límite. Con el armado en disco, el dinero real
  sigue armado; el agente concilia las posiciones con Binance al arrancar y cierra las que vencieron mientras estuvo
  apagado. Después, `revisar.sh`. Comprobado el 6-nov (el dueño reinició por el kernel nuevo y todo volvió solo) y
  el 7-nov a las 09:49 UTC, con el dinero real armado (reinicio de prueba del dueño): el relé y el agente arrancaron
  solos en el mismo segundo, Telegram escuchando a los 4 s, y al minuto la revisión dio todo OK (14 fuentes, el canal
  de anuncios conectado, las 8 conexiones tibias, 0 errores). En el primer minuto las fuentes y el canal todavía están
  arrancando: `revisar.sh` espera sola a que el agente lleve 60 s.
  Lo que no vuelve solo: un estudio o medidor lanzado a mano en `~/diag` (no es producción).

### Vigilancia (qué revisar en cada pasada, también la vigilancia programada de cada 3 h)

La tarea programada "Vigilancia del simulacro de TRADING" corre **cada 2 h, las 24 h** (desde el 6-nov, pedido del
dueño: "vigila constantemente todo sin q yo te lo diga. mantene traspaso y promt actualizado"). Su texto ya describe
a Tokio como motor. Lee este archivo primero, y lo que manda es esta lista:

1. **PC** (`%LOCALAPPDATA%\TRADING`):
   - `state\supervisor.json`: modo `solo_tablero`, versión = `origin/main`, publicación `ok`.
   - `state\tokio.json`: "agente" de menos de 2 min; "rele" con `telegram.conectado`.
   - `state\tablero-tokio.json`: de menos de 5 min.
   - El tablero (http://127.0.0.1:18765) dice que funciona y muestra el dinero real y los módulos; el detalle de las
     cuentas de Tokio, en `/detalle`.
   - Que el agente de la PC no corra es lo normal.
2. **Tokio** (por SSH, con el ssh de Git):
   - Primero `bash ~/trading/deploy/tokio/revisar.sh`: todo OK (si algo da FALLA, eso manda). Lo de abajo es el detalle.
   - `systemctl is-active rele-tokio agente-tokio vigia-red.timer`.
   - `free -m`: más de 200 MB libres.
   - `cd ~/trading && git log --oneline -1` = `origin/main`. Si quedó atrás y el CI está verde:
     `bash ~/trading/deploy/tokio/instalar-agente.sh`. **Con dinero real armado** (`cat ~/.trading/ARMADO.json`:
     `"simulacro": false`), instalar solo sin posiciones reales abiertas (`real/binance-futuros/estado.json`,
     `positions` vacío; G-7): reinstalar reinicia al agente. Nunca armar, desarmar ni tocar el armado: es del dueño.
     Después de instalar, la prueba de punta a punta **en la PC** (punto 5).
   - `sudo journalctl -u agente-tokio -u rele-tokio --since -3h --no-pager -p warning` y `sudo journalctl -t vigia-red`.
     "poca memoria en el servidor" en el registro: mirar si coincide con `unattended-upgrades`
     (`sudo tail /var/log/unattended-upgrades/unattended-upgrades.log`) y anotarlo (sección 11).
   - `[ -f /var/run/reboot-required ]`: el sistema pide reinicio (lo hace el dueño, a hora tranquila y sin posiciones
     reales abiertas). **No hay ningún reinicio programado** (revisado el 6-nov: crontab, `cron.d`, temporizadores y
     `Automatic-Reboot`): el comando del 6-nov movió las actualizaciones automáticas a las 15:00 UTC (medianoche en
     Tokio y Corea) y no reinician nada. Que siga así: cada reinicio deja ~1 min sin escuchar Upbit ni vigilar
     posiciones, y la memoria del agente ya no crece.
   - **Memoria del agente y freno del bucle:** `bash ~/trading/deploy/tokio/memoria.sh` (solo lee). Dice cuánto lleva
     encendido, la memoria del servicio, dónde está (arena principal, arenas de hilos, resto de Python) y el retraso
     del bucle en el último minuto. Anotarlo en §12 con las horas de encendido.
     - Antes del #66 (6-nov): 160 MB a los 57 min, 165 MB a los 73 min y 218 MB a las 3,8 h (6-nov 09:03 UTC). Otro
       arranque: 186 MB a las 1,7 h, con 9 arenas de hilos de glibc que tenían 68 MB (sección 11).
     - Desde el #66 (`MALLOC_ARENA_MAX=2`): tiene que quedar 1 arena de hilos como mucho, y menos memoria a las mismas
       horas. Si a las 6-10 h sigue subiendo ~20 MB/h, investigar el "resto" (objetos de Python).
     - A los 400 MB (`MemoryHigh`) el sistema empieza a frenar al agente, y a los 500 MB lo reinicia.
     - Bucle: la mediana es ~0,2 ms y el máximo de cada minuto 10-45 ms. Un máximo de más de ~100 ms que se repite
       (cada 2 min, cada 15 min) es trabajo pesado que frena las entradas al instante: buscarlo (sección 11).
   - **Conexiones tibias con Binance** (pedido del dueño, 7-nov: "mantener las conexiones con binance en tibio"; #69):
     `PID=$(systemctl show -p MainPID --value agente-tokio); ss -tnp state established | grep "pid=$PID," | grep ':443'`
     tiene que mostrar ~8 conexiones a máquinas de AWS en Tokio (`ec2-…ap-northeast-1`): 4 del dinero real y 4 del
     simulacro. Si no hay, la primera entrada al instante paga ~45 ms de saludo TLS: revisar el registro del agente.
   - Si no responde tres veces seguidas: avisar al dueño para que lo reinicie desde la consola de Lightsail, y no
     tocar nada más.
3. **Simulacro de Tokio** (o el dinero real, si el dueño lo armó):
   - `cd ~/trading && TRADING_HOME=/home/ubuntu/.trading .venv/bin/python -m tradingagent informe-simulacro --dias 1`
     (y `--cuenta practica`; con dinero real, `--cuenta real`: sus montos no se publican nunca, G-8).
   - Las decisiones y operaciones están en `/home/ubuntu/.trading/simulacro/binance-futuros/`, `real/binance-futuros/`
     y `paper-vivo/`.
   - El simulacro (100 USDT de mentira): con el dinero real armado es `~/.trading/SIMULACRO.json` (corre junto al real,
     vence a los 30 días); sin dinero real, `ARMADO.json`. Si vence, en el servidor, con
     `TRADING_HOME=/home/ubuntu/.trading`: `armar-real --simulacro --dias 30`. Con el dinero real armado ese comando
     escribe solo `SIMULACRO.json` y nunca toca el armado real (reinicia al agente: hacerlo sin posiciones reales).
   - El simulacro nunca opera lo del dinero real: en `simulacro/.../decisiones`, los listados de Upbit dicen
     `la_opera_el_dinero_real`. Si aparece una entrada del simulacro en una estrategia del dinero real, avisar.
   - **Dinero real solo para las estrategias del armado** (sección 6; `cat ~/.trading/ARMADO.json` → `estrategias`;
     el 7-nov: `listado_upbit` y `listado_binance`): cada entrada real tiene que ser de una de esas; las demás dicen
     `estrategia_no_armada` en `real/.../decisiones`. Si aparece otra estrategia operando con dinero real, frenar primero
     (`touch ~/.trading/KILL`) y avisar al dueño.
   - **Cortos al instante** (deslistado, etiqueta de vigilancia, fin del soporte): en la decisión de entrada,
     `analisis.alarmas_del_libro` dice qué alarmas del libro habrían frenado (no frenan: REGLAS B-4). Anotar en §12 cuáles
     aparecen en cada caso real: con ~10 se revisa si la excepción se sostiene.
4. **Avisos oficiales:** medir la demora de punta a punta en Tokio (publicado, recibido, decisión y entrada).
   - En los listados de Upbit al instante, mirar `variante-ia.jsonl`: es la prueba A/B que pidió el dueño (entrar al
     instante contra esperar a la IA).
   - Canal de Binance en Tokio: 3 avisos medidos (§12): 41 s (TradFi, 6-nov 01:00:00 UTC, hora redonda), 1,2 s (03:06:23)
     y 2,6 s (08:00:00, hora redonda). En la PC eran 1,2-1,8 s. **Lo importante:** si los deslistados se programan a
     hora redonda y llegan tarde, el canal no alcanza para entrar en 2-5 s. En cada aviso de Binance anotar la hora de
     publicación, si es redonda, y la demora, hasta tener ~10. Se ven con
     `grep -h '"source":"binance.ws"' ~/.trading/journal/*.jsonl | grep '"kind":"news"'` (`published_at`, `age_s`).
   - En el registro del agente, cada entrada al instante deja sus tiempos por etapa: "listado de Upbit al instante
     (X): esperó N ms · candado · contrato · velas, libro y precio · orden a los M ms". Lo mismo para los deslistados,
     la etiqueta de vigilancia, la advertencia de Upbit y el fin del soporte.
   - **Bithumb** (`bithumb.avisos`, desde el 7-nov): la demora de cada aviso, de publicado a recibido, tiene que ser
     ~1 s (`grep -h '"source":"bithumb.avisos"' ~/.trading/journal/*.jsonl | grep '"kind":"news"'`: `age_s`). Si da
     minutos, su lista dejó de ser fresca: el freno de 5 s del fin del soporte no deja entrar, pero hay que buscar otra
     fuente. Si la fuente aparece "abierta" o con HTTP 403 en el latido, Bithumb nos bloqueó: avisar al dueño.
5. **Después de cada cambio**, la prueba de punta a punta **en la PC** (pedido del dueño, 6-nov: "para aliviar la
   carga de Tokio todas las pruebas ... desde mi PC pero simulando los tiempos de Tokio"):
   `python deploy/tokio/prueba_listado.py --pc`, desde el worktree con su `.venv` (`PYTHONPATH=src`).
   - Corre un agente y un relé aparte en la PC, contra un Binance falso local que contesta en ~3 ms (como Binance desde
     Tokio). No toca el simulacro, Telegram ni la cuenta real; solo usa la clave de la IA de la PC (centavos).
   - Tiene que terminar con 4 entradas (SOL, el corto de DOGE, el de XRP por la advertencia de Upbit y el de ADA por
     el fin del soporte de Bithumb, que lee el lector de verdad contra un Bithumb falso local), las 2 últimas (ETH y
     PEPE) frenadas solo por el sobre (`maximo_de_posiciones`: deja 4), la IA confirmando y sin errores ("entradas: 4
     de 4 posibles ... avisos sin su regla: ninguno").
   - Medido el 6-nov en la PC: orden a los 148 ms en la primera (conexiones en frío), 48 ms y 33 ms en las otras; IA
     1,3-2,6 s. El 7-nov: SOL 95 ms (en frío), DOGE 35 ms, XRP 44 ms, ADA 48 ms (leído a <1 s de publicado); la IA
     confirmó los títulos coreanos en 1,1-1,2 s; con 4 posiciones abiertas, el candado 5-6 ms (antes 156 ms, ver §12).
   - En Tokio (`~/trading/.venv/bin/python ~/trading/deploy/tokio/prueba_listado.py`, Binance real, suma ~150 MB
     mientras corre) solo cuando cambia el servidor, Python o las dependencias, o antes de armar dinero real.

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
   - El país en el nombre legal de una empresa ("Jerash Holdings (US), Inc.", 8-K del 5-nov) no es la cripto US:
     un "(US)", "(UK)", "(HK)"... seguido de Inc, Ltd, Corp, etc. no cuenta como ticker.
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
| `python deploy/demo_corto.py` | Una ida y vuelta de un CORTO de verdad en la DEMO con el venue del dinero real (como un deslistado: tope de 2 %, stop de emergencia al +30 %, salida a los 6,5 s). Plata ficticia, carpeta temporal. La puede correr Claude |
| `armar-real` | Arma el dinero real en Binance con todo el saldo. **Solo lo corre el dueño:** pide la frase exacta (REGLAS A-2). `--exchange mexc` para MEXC |
| `armar-real --simulacro --dias 7` | El simulacro local: el operador real con el libro real de Binance y órdenes simuladas en la PC, con 100 USDT de mentira (nunca el saldo real: se publica). `--capital N` para otro capital, `--demo` contra la demo de Binance, `--exchange mexc` en MEXC |
| `informe-simulacro` | Calibración: llegada de las noticias por fuente, de la noticia a la entrada, acierto, ganancia neta por categoría, deslizamiento. `--cuenta practica|demo|real`, `--dias N` |
| `informe-fiscal` | Registro fiscal (ARCA): operaciones, ganadas, volumen, comisiones y resultado neto por mes de Argentina. `--cuenta real` (por defecto), `--anio 2026`, `--csv archivo` para el contador. Solo en la PC |
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

### Dinero real desde Tokio: solo los listados de Upbit (pedido del dueño, 6-nov); lo arma el dueño

Pedido del dueño (6-nov): "el módulo de inversión automática (y luego preguntar a la Api IA) de upbit (escucha por
telegram) ya quede funcionando con dinero real (todo el de la cuenta binance)", y lo demás en pruebas hasta entenderlo.
Para eso está el **armado por estrategia** (REGLAS v3.4, A-2): `armar-real --estrategias listado_upbit`. Solo esa
estrategia abre con dinero real; las demás siguen en la **práctica** (`paper-vivo`, 120 USDT simulados). El simulacro
de 100 USDT deja de correr mientras haya dinero real armado (el armado es uno solo).

**Tamaño:** todo el saldo de la cuenta de futuros es el capital. Cada listado arriesga el 3 % del saldo al stop de 10 %:
entra con ~30 % del saldo (L-3; más pide ~30 operaciones reales y el OK del dueño). Siguen el freno del día (2 %), el de
la semana (4 %) y el halt por caída (8 %). La IA lee el aviso después de entrar y, si no lo confirma, cierra (L-1).

1. ✅ Servidor de ~900 MB con el vigía de la red; la clave de trading está restringida a la IP de Tokio, sin retiros
   ni transferencia interna (verificado leyendo sus permisos).
2. En Binance (el dueño): la billetera de futuros USDⓈ-M tiene **solo** lo que se quiere arriesgar (el programa usa
   todo ese saldo, A-3), y la cuenta está en modo de posición unidireccional y de un solo activo.
3. Fusionar el PR del armado por estrategia (protegido, lo fusiona el dueño). Claude lo instala en Tokio y corre la
   prueba de punta a punta (`prueba_listado.py`).
4. En el servidor, por SSH (los corre el dueño):
   - `cd ~/trading && .venv/bin/python deploy/tokio/preparar_env.py --trading < /dev/null`: pasa la clave de trading
     al agente.
   - `TRADING_HOME=/home/ubuntu/.trading .venv/bin/python -m tradingagent binance-real-check`: la prueba sin riesgo
     con órdenes (una que no se puede ejecutar y un stop lejísimos; nada queda abierto).
   - `TRADING_HOME=/home/ubuntu/.trading .venv/bin/python -m tradingagent armar-real --estrategias listado_upbit`: su
     frase. Vence a los 30 días; el agente se reinicia solo (si no, `sudo systemctl restart agente-tokio`).
   - Telegram tiene que decir "💵 DINERO REAL armado en Binance ... solo listados de Upbit al instante; lo demás, en
     práctica". `/estado` lo repite.
5. El simulacro de los demás módulos va junto al dinero real (REGLAS v3.5): `armar-real --simulacro --dias 30` con el
   dinero real armado escribe `SIMULACRO.json` (local, 100 USDT de mentira) y nunca toca el armado real; se saca con
   `armar-real --desarmar --simulacro`. Nunca opera lo que opera la plata. La práctica está apagada en Tokio.
6. Frenar: /pausa por Telegram, el KILL de la PC (se copia a Tokio en ~1 s), o por SSH `touch ~/.trading/KILL`,
   `armar-real --desarmar` o `cerrar-todo` en el servidor. Volver al simulacro: `armar-real --desarmar` y
   `armar-real --simulacro --dias 30`.
7. La telemetría al tablero no lleva montos reales. El tablero completo los trae por SSH (cifrado) y se ven solo en
   la PC. Lo publicado nunca los lleva.

### Sumar los listados de Binance al instante al dinero real (REGLAS v3.6, 6-nov)

1. ✅ Fusionado (#76) e instalado en Tokio el 6-nov a las 23:38 UTC (`b42c9ae`). Prueba de punta a punta en Tokio
   con el Binance real: el listado de Binance (PEPE en 1000PEPEUSDT) ordenó a los 31 ms de recibido (1,2 s después
   de publicado) y la IA confirmó en 1,06 s; Upbit 26-63 ms, el deslistado 22 ms; sin errores.
2. Con **ninguna posición real abierta** (el agente se reinicia), el dueño suma la estrategia con su frase:
   `TRADING_HOME=/home/ubuntu/.trading .venv/bin/python -m tradingagent armar-real --estrategias listado_upbit,listado_binance`.
   Telegram tiene que decir "solo listados de Upbit al instante, listados de Binance al instante". El simulacro
   de al lado la suelta solo (nunca opera lo que opera la plata).
3. Qué mirar en cada listado de Binance: en `real/binance-futuros/decisiones/`, `listado_binance_tarde:X.Xs` si el
   canal llegó tarde (más de 2,2 s: no entra, la opera la regla general con la IA solo en el simulacro) o
   `ya_subio:+X%`; en el diario, la demora del canal (`binance.ws`, de publicado a recibido). Con ~10 listados se
   revisa el freno (2,2 s) con lo medido.

### Sumar los deslistados de Binance y los avisos coreanos al dinero real (REGLAS v3.7, 7-nov)

Pedido del dueño (7-nov): "a Deslistados preparemos todo y hagamos las pruebas q tengamos q hacer y pasmos a dinero
real [...] si fin de soporte o corto por advertencia upbit es bueno preparemos todo tambien para ya entrar en real".

| Estrategia | Qué hace | Tamaño | Evidencia |
|---|---|---|---|
| `deslistado_corto` | corto apenas llega "Binance Will Delist …" (hasta 60 s, si no cayó 4 %), tope de orden 2 %, stop 20 %, 4 h | 0,10 veces el saldo: 2 % al stop | 49 monedas: mediana +17,96 %, 84 %; con tope de 2 % se llena 43 de 45 |
| `advertencia_upbit` | corto apenas llega la advertencia de Upbit por Telegram (hasta 30 s, si no cayó 3 %), stop 10 %, 4 h | 0,20 veces: 2 % al stop | 42 monedas: mediana +2,02 %, 69 %, las dos mitades positivas |
| `fin_soporte_corto` | corto con el primer fin del soporte de Upbit (Telegram) o Bithumb (su web, cada 1 s), hasta 5 s si no cayó 6 %, stop 10 %, 5 min | 0,10 veces: 1 % al stop | 25 monedas: mediana +3,73 %, 80 %; Bithumb sin probar en vivo |

1. ✅ Fusionado (#80) e instalado en Tokio el 7-nov a las 09:13 UTC (`953bb9b`), sin posiciones reales abiertas. Prueba
   de punta a punta allá con el Binance real: deslistado 27 ms, advertencia 24 ms, fin del soporte de Bithumb 23 ms de
   recibido (350 ms de publicado), la IA confirmando en ~1 s, sin errores. Las conexiones tibias con Binance (pedido
   del dueño: "mantener las conexiones con binance en tibio") corren para el dinero real y para el simulacro: 8
   conexiones abiertas (4 por grupo).
2. En el servidor, por SSH (los corre el dueño), con `TRADING_HOME=/home/ubuntu/.trading` y desde `~/trading`:
   - `.venv/bin/python -m tradingagent binance-real-check`: la prueba sin riesgo; desde el 7-nov valida también la venta
     en corto IOC con el tope de 2 % (en la demo ya pasó).
   - Con ninguna posición real abierta, su frase: `.venv/bin/python -m tradingagent armar-real --estrategias
     listado_upbit,listado_binance,deslistado_corto,advertencia_upbit,fin_soporte_corto` (o solo las que quiera).
     Telegram tiene que nombrar las cinco. El simulacro de al lado las suelta solo (nunca opera lo de la plata).
3. Qué mirar en cada caso real: en `real/binance-futuros/decisiones/`, los frenos (`*_tarde:Xs`, `ya_cayo:-X%`), la
   demora del canal (`aviso.demora_s`), las alarmas del libro que no frenaron (`analisis.alarmas_del_libro`) y la
   segunda lectura de la IA (`variante-ia.jsonl`). Con 4 posiciones abiertas el sobre no deja abrir otra: un aviso de
   varias monedas puede llenar el cupo. Medido (§12): de 291 listados desde 2025, solo 1 habría encontrado las 4
   ocupadas por cortos; limitar los cortos a 3 a la vez costaba 18 de 117 cortos. Queda así.


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
  −3 %). Si todavía no cayó más de 4 %, +7 a +12 % de media aunque se llegue a los 3-5 s. El 7-nov, con 49 monedas
  (`estudios/hipotesis_direccionales.py`): mediana +17,96 %, 84 %; entrar a 0,1, 1,3 o 2 s da lo mismo; el tope de la
  orden de 0,5 % dejaba afuera las mejores (con 2 %, 43 de 45 y +12,3 % por aviso: REGLAS L-11).
- **Resultados y el control de liquidez** (5-nov, las 60 operaciones del estudio): el freno de Kyle (> 4 veces el impacto de precio de la hora previa) deja afuera 14 (23 %). Esas dieron mediana +0,34 % y 8 de 14 positivas; las que entran, mediana +1,41 % y 31 de 46. El freno se queda: saca las más flojas.
- **6-K de empresas extranjeras** (5-nov, `estudios/eventos_6k.py`, 473 avisos de 15 empresas con perpetuo: TSMC, Alibaba, SK Hynix, Novo Nordisk, Sony, ARM, ASML...): solo 4 % mueve el perpetuo a los 2 min (al azar 2,5 %) y seguirlo no rinde (n 12, media +0,25 %). Publican los resultados en su país antes del 6-K: cuando llega a la SEC, el mercado ya reaccionó. No se opera. **Coinbase:** su página de estado solo trae incidentes técnicos, no listados (no sirve de fuente).
- **Otros 8-K de la SEC** (5-nov, `estudios/eventos_sec.py`, 1.136 8-K sin resultados de 141 empresas con perpetuo desde febrero): mueven el precio (impulso ≥ 0,5 % a los 2 min en 9 % de los casos, contra 1,5 % en horas al azar), pero seguirlo no rinde (n = 44, media +0,6 %, 52 % positivas; por ítem, muestras de 8 a 23). No se opera.
- **Etiqueta de vigilancia de Binance** (5-nov, `docs/ESTUDIO-DESLISTADOS.md`, 31 monedas de 14 avisos desde 2025): cae −0,7 % a los 2 s y −4 % a los 5 s; corto a los 2 s si no cayó más de 2 %, stop 10 %, 4 h: mediana +3,6 %, 85 % positivas (2025 flojo, 2026 18 de 18). A los 3-5 s ya no hay ventaja.
- **Upbit y Bithumb, avisos negativos, operación por operación** (7-nov, `estudios/hipotesis_direccionales.py
  advertencias`; reemplaza al del 5-nov de abajo, que tenía 13 y 6 casos): advertencia de Upbit (42 monedas) sin salto,
  baja de a poco: corto a los 2 s, 4 h, mediana +2,02 %, 69 %; la de Bithumb (43) +0,90 %, 53 %. Fin del soporte, el
  primer aviso de Upbit o Bithumb (25): cae en los primeros segundos; a los 2 s, 5 min, mediana +3,73 %, 80 %; a los
  10 s, +0,60 %. Son las reglas de `decision/korean_notices.py` (REGLAS v3.7, C-4).
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
15. **Listados de Binance al instante: HECHO** (REGLAS v3.6; la arma el dueño, sección 6). Queda: medir el canal en
    cada listado real y revisar el freno de 2,2 s con ~10 casos. El estudio (6-nov, `estudios/binance_listados_segundos.py`): el perpetuo
    salta a los ~2,3 s del aviso y el canal de anuncios llega a Tokio a los 1,2-1,8 s. Comprar a los 2 s (sin la IA,
    que tarda 1,1-1,8 s) y salir como Upbit da mediana +7,5 %; a los 2,5 s, +2,0 %. Falta: (a) medir el canal con
    listados de verdad, sobre todo los publicados a hora redonda (uno llegó a 2,6 s y un TradFi programado a 41 s);
    (b) la regla con freno de demora (entrar solo si llegó a ≤ 2 s y el precio todavía no saltó), en el simulacro de
    Tokio primero. Excepción a C-4 y A-5: PR protegido, lo aprueba el dueño. ~3 por mes en 2026.
17. **Fase 2 de fuentes (doble fuente, plan del 7-nov con el dueño):**
    - **Binance, activo-activo en vez de "degradarse":** el WebSocket (todos los catálogos, ~1,2 s) más UN lector del
      CMS con el pedido sin catálogo (los 7 juntos), rotando formas de la dirección para saltar la copia del CDN, en
      lugar del lector actual del relé (solo el 48): mismo ritmo, así no se gasta más del límite por IP (L-7), y cubre
      deslistados (161), vigilancia (49) y HODLer (128). La deduplicación de la ingesta ya hace que un aviso que
      llega por los dos se procese una sola vez. **Hecho el 7-nov** (§12): el relé intercala el 48 y la lista sin
      catálogo, con los mismos pedidos.
    - **Categorías nuevas:** si el WebSocket o el CMS traen un número de catálogo que no conocemos, avisar una vez por
      Telegram (el "DefaultLoggingHandler" del plan) y seguir. **Hecho el 7-nov** en el WebSocket (§12).
    - **Registro de módulos:** las reglas al instante ya son módulos (`decision/*.py` con su `notice()`); pasar el
      despacho de `live.on_news` a una lista registrada (fuente, regla, manejador), así sumar un módulo es sumar una
      línea. Toca el camino del dinero real y `test_rules`: PR protegido aparte, con la prueba de punta a punta.
      Se hace con el próximo módulo nuevo (hoy no hay ninguno en camino).
    - **Upbit:** sin segunda fuente (descartado el 7-nov, §12: la PC se puede apagar). **Bithumb:** una sola fuente
      (gw).
    - **Regla de Upbit por tipo** (§12, 7-nov): REGLAS v3.9 en un PR protegido (freno de 25 % y el listado nuevo hasta 1 s); lo fusiona el dueño y después se instala con el procedimiento de §3.
16. **Bithumb al instante** (estudio del 6-nov, `estudios/otros_listados_segundos.py`): sus altas en wones mueven el
    perpetuo como Upbit (comprar a 1 s: mediana +4,3 %), pero en 2026 la ventana es de ~1 s. El canal: su API oficial
    de avisos (`api.bithumb.com/v1/notices`, 130 ms desde Tokio; la página da 403; su Telegram solo da precios).
    Probado el 6-nov: su API pública pasa por Cloudflare con caché de 4 h que ignora los parámetros (la copia tenía
    41-57 min); su canal oficial de Telegram (@bithumbexchangedata) solo da alertas de precio (800 mensajes, ningún
    alta). Lo que sirve: `gw.bithumb.com/feed/v2/customer-support/articles/notices/recent`, la que usa su web de
    trading, sin caché, 120 ms desde Tokio. **Midiendo en Tokio** desde el 6-nov 23:52 UTC por 72 h
    (`~/diag/bithumb_demora.py`, nice 19, ~20 MB, un pedido por segundo a cada fuente; resultados en
    `~/diag/bithumb-demora.jsonl`: filas `nuevo` con la demora de cada fuente; `http` 403 seguido = nos bloquearon).
    No matarlo. Si gw muestra los avisos a ~1 s, la regla como Upbit leyendo gw (PR protegido, lo aprueba el dueño).
    **Desde el 7-nov el agente ya lee gw** (`bithumb.avisos`, cada 1 s, REGLAS v3.7), pero solo para el fin del
    soporte (corto; en los avisos conjuntos con Upbit, Bithumb publica primero o junto): sus listados todavía no se
    operan. Cuando el medidor (o el diario del agente) muestre la demora real de gw en avisos de verdad, decidir la
    regla de listados de Bithumb con su ventana de ~1 s.
    Pagas, si no: cryptolisting.ws (WebSocket de Binance, Upbit y Bithumb, precio a pedido) o PKCryptoListing (dice
    < 1 s, tiene plan gratis); las dos piden una cuenta del dueño.

## 11. Detalles que muerden (Windows y MEXC)

**Tokio sin red (5-nov-2026, 23:02 a 23:46 UTC).** El servidor de 416 MB, sin swap, estaba al límite de memoria: pip
instalando mientras el agente arrancaba. A las 22:23 `systemd-networkd` no pudo poner una ruta ("ens5: Could not set
route: Connection timed out"), dio la interfaz por caída ("ens5: Failed") y dejó de renovar la IP. A los ~40 min la
concesión venció y el servidor quedó sin red. El agente siguió vivo (latido cada 15 s), pero sin fuentes ni Telegram,
y sin SSH ni telemetría desde afuera. Lo resolvió el dueño: instancia nueva desde una instantánea, con ~900 MB y la
misma IP (estática). Las claves del servidor cambiaron: la PC tuvo que aceptar la huella nueva (ED25519
`SHA256:enk3HfrP3BHU2xGLZZHFExFRCxXAgEVsIfbBFYPrp1g`).

Para que no vuelva a pasar:

1. **Memoria acotada:** los dos servicios tienen `MemoryHigh` / `MemoryMax` (agente 400 / 500 MB, relé 150 / 200 MB)
   y `OOMScoreAdjust=-500`. Si uno se pasa, el kernel mata solo a ese y systemd lo relanza, sin ahogar al sistema.
2. **El instalador** corre pip solo si cambiaron las dependencias, y con los servicios parados.
3. **Aviso:** el supervisor de la PC en "solo tablero" avisa por Telegram si Tokio no manda nada en 3 min (y cada
   30 min si sigue), y cuando vuelve (`Supervisor.watch_tokio`).
4. **Vigía de la red** (`deploy/tokio/vigia-red.*`): cada minuto, si la interfaz quedó "failed" o sin ruta, la
   reconfigura o reinicia `systemd-networkd`. Es un cambio del sistema: **lo instaló el dueño el 6-nov a las 01:26
   UTC** (`systemctl is-active vigia-red.timer`).
5. **La instancia vieja ya no está** (el dueño, 6-nov). Si algún día se arma otra desde una instantánea, la anterior
   tiene que quedar apagada: tienen la misma sesión de Telegram y el mismo bot, y si las dos tienen red se pisan (dos
   lectores del bot, y la sesión de Telegram puede quedar revocada).

Ver qué pasó en un arranque anterior: `sudo journalctl --list-boots` y `sudo journalctl -b -1 ...`. Ojo con
`--since`: sin fecha toma la de hoy.

**La actualización automática de Ubuntu (6-nov, 06:10 UTC):** `unattended-upgrades` instaló sola unos 20 paquetes
(apt, curl, git, certificados y un núcleo nuevo).

- Durante ~10 min la memoria libre bajó a 59-186 MB: tres procesos de ~170 MB y otro de 99 MB.
- Reinició `systemd-networkd` (06:12:41) y dejó el sistema pidiendo un reinicio por el núcleo nuevo
  (`/var/run/reboot-required`).
- Nuestros servicios aguantaron (topes de memoria y `OOMScoreAdjust`), sin muertes por memoria y con la red bien.
- El horario es malo: las 06:10 UTC son las 15:10 en Corea, hora de avisos de Upbit, y la actualización compite por
  memoria y CPU con las entradas al instante.
- Desde el #64, el latido trae la memoria libre del servidor (el tablero la muestra en rojo con menos de 150 MB), y
  con menos de 120 MB queda el aviso "poca memoria en el servidor" en el registro del agente.

**Aplicado por el dueño el 6-nov a las ~09:54 UTC** (los tres cambios de abajo):

- swap de 1 GB activo y en `/etc/fstab`;
- `apt-daily-upgrade.timer` a las 15:00 UTC (`/etc/systemd/system/apt-daily-upgrade.timer.d/hora.conf`);
- servidor reiniciado con el núcleo nuevo (6.8.0-1066-aws).

Todo volvió solo en 10 s: relé, canal de Upbit, bot, anuncios de Binance y la PC. No saltó el aviso de "Tokio
callado" (menos de 3 min). Después, la prueba de punta a punta con `main` dio órdenes a los 25-68 ms y sin errores.

Los comandos, por si hay que rehacerlo en otro servidor:

1. Un swap de 1 GB, para que estos picos no aprieten:
   `sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab`
2. Las actualizaciones automáticas a las 15:00 UTC (medianoche en Corea, mañana en Nueva York, pocos avisos):
   `sudo mkdir -p /etc/systemd/system/apt-daily-upgrade.timer.d && printf '[Timer]\nOnCalendar=\nOnCalendar=*-*-* 15:00\nRandomizedDelaySec=10min\n' | sudo tee /etc/systemd/system/apt-daily-upgrade.timer.d/hora.conf && sudo systemctl daemon-reload`
3. Reiniciar el servidor a esa hora tranquila para el núcleo nuevo: `sudo reboot`. Los servicios arrancan solos, el
   simulacro y los stops quedan guardados, y la PC avisa por Telegram si Tokio no vuelve en 3 min.

**Dos trampas encontradas con la prueba de punta a punta (6-nov):**

- **El SDK de la IA (`import anthropic`) tarda 1,2 s en cargar en Tokio.** Si lo carga la primera noticia, frena el
  bucle ese tiempo, entradas al instante incluidas. Ahora se carga al arrancar, en otro hilo
  (`LlmClassifier.warm_up`). Cualquier import pesado nuevo tiene que ir al arranque, no a la primera noticia.
- **La deduplicación juntaba dos avisos distintos con la misma plantilla** (dos listados de Upbit de otra moneda: 0,62
  de parecido, umbral 0,6). En 180 días pasó dos veces (KMNO y BSB, RSR y GTC). Ahora otros tickers en el título u
  otro enlace del mismo aviso oficial nunca son "la misma noticia" (`ingestion/dedup.py`).

**Memoria y freno del bucle (6-nov, medido en Tokio con `deploy/tokio/memoria.sh` y el latido):**

- **La memoria del agente es casi toda de los asignadores** (~180 MB a las 1,7 h de arrancar): ~30 MB en la arena
  principal de glibc, 68 MB en 9 arenas de hilos (glibc crea una por hilo: los de `asyncio.to_thread`, el diario y las
  alertas) y ~80 MB de objetos de Python. La misma réplica del agente en la PC (Windows, otro asignador) usa ~105 MB.
  El SDK de la IA solo ocupa ~45 MB (se carga al arrancar, por la precarga). Arreglo (#66): `MALLOC_ARENA_MAX=2` en
  `agente-tokio.service`: todos los hilos comparten 2 arenas.
- **La pasada de aprendizaje frenaba el bucle:** corre cada 15 min dentro del agente y rehacía en el bucle la ventana
  de deduplicación del recolector (~1 ms por noticia, MinHash). En Tokio, 122 ms de una vez (6-nov 11:30:16); en la PC,
  300-365 ms. Una entrada al instante que llegara justo ahí esperaba eso de más. Ahora va en un hilo
  (`research/collector._window`); en la PC el máximo bajó a 37-99 ms.
- **El tablero exportado** (cada 2 min, en un hilo) releía el entorno en cada línea del registro que redactaba (1.008
  veces por tablero: el 85 % de su costo). Mientras tanto el bucle se demoraba 60-95 ms (el GIL de Python: un hilo con
  CPU le saca turnos al bucle). Ahora lee los secretos una vez por tablero (`core/redact.redactor`).
- **Regla:** todo trabajo pesado va a un hilo, y aun así compite por el GIL: cuanta menos CPU, mejor. Medirlo con el
  retraso del bucle del latido antes y después (`memoria.sh`). Bajar el intervalo del GIL a 1 ms no mejoró nada (PC).
- **Conexiones en frío con Binance:** desde Tokio, una conexión nueva a `fapi.binance.com` tarda ~45 ms (casi todo el
  saludo TLS; `curl`, 6-nov) y una abierta ~2 ms. aiohttp cierra las que no se usan en 15 s, y una entrada al instante
  pide velas, libro y precio a la vez y después la orden: la que llegaba en frío pagaba ~45 ms de más (en la prueba de
  punta a punta, la primera entrada 68 ms y la segunda 27). Ahora el agente deja 4 abiertas con un pedido liviano cada
  10 s (`Agent._keep_warm`, `/fapi/v1/time`, ~24 de peso por minuto de 2.400).

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
  - **el stop Algo recién puesto también se ve con demora:** desde Tokio (6-nov) la consulta salía ~3 ms después de
    ponerlo y Binance contestaba -2013 "Order does not exist"; `binance-real-check` fallaba sin que nada estuviera
    mal (desde la PC, a ~0,3 s, siempre aparecía). Ahora la prueba espera hasta 5 s a que aparezca, y si falla
    limpia recién a los 6 s de puesto (D-6). Medido en la prueba del dueño (6-nov 13:10 UTC): el stop apareció a
    los 1,08 s y su cancelación se vio a los 1,53 s. El operador real no consulta el stop al ponerlo (lo revisa la
    conciliación, cada 30 s);
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
| 5-nov | Canales oficiales de Telegram (noticias generales) | Upbit 0-2 s del sitio (en uso); Binance +160 s de mediana (el WebSocket da 1,2-1,8 s); Cointelegraph +25 min de su RSS; CoinDesk, The Block y Decrypt sin canal; Bithumb solo señales de precio; Coinbase anuncia en X | solo Upbit; Coinbase por X es decisión del dueño (API paga) | `estudios/telegram_canales.py` |
| 5-nov | Canal de anuncios de Binance | de publicado a recibido 1,2-1,8 s (5 avisos) | medido | sección 2 |
| 5-nov | Camino interno del agente | de un aviso a la orden: 220 → 30 ms (balde de fichas) | hecho (PR #49) | sección 9 |
| 5-nov | Control de peso de la API de Binance | lee `X-MBX-USED-WEIGHT-1M`; > 80 %: lo no urgente espera | hecho | `marketdata/peso.py` |
| 5-nov | Segunda lectura de la IA para las reglas sin IA | si no confirma el aviso, cierra la posición | hecho | `live.py` |
| 5-nov | Registro fiscal (ARCA) | `informe-fiscal`: resultado y volumen por mes, CSV para el contador | hecho | `research/fiscal.py` |
| 5-nov | **Relé de Tokio instalado** | túnel firmado PC↔Tokio OK (ida y vuelta 288 ms); desde Tokio Binance 48 ms; lo empujado no se acelera; **Upbit bloquea las IP de AWS (403)**; batchOrders con stop: −4120 | en marcha: lee la lista de Binance (respaldo); la ganancia grande sería operar desde Tokio (decisión del dueño) | `docs/RELE-TOKIO.md` |
| 5-nov | **Upbit en el perpetuo, operación por operación (31)** | a los 2 s ya +15 % (tope de Binance); comprar a los 2 s: 5 min mediana +1,45 %, media +4,85 %; a los 5 s −1,72 %; a los 9 s (la PC) −2,12 % | regla al instante desde Tokio por Telegram (`listado_upbit`, PR protegido: la aprueba el dueño) | ESTUDIO-LISTADOS, `estudios/upbit_segundos.py` |
| 5-nov | **Upbit: ¿al instante o con la IA?** (31, demoras de Tokio) | A al instante (1,05 s): mediana +9,16 %, media +12,43 %, 71 %; B esperando a la IA (2,85 s): +1,46 %, +5,76 %, 55 %; 100 USDT → 145,8 (A, 1 %) o 296,6 (A, 3 %) contra 119,0 (B, 1 %); sin falsos positivos | A (la regla al instante); subir a 3 % lo decide el dueño con listados reales; A/B en vivo en `variante-ia.jsonl` | ESTUDIO-LISTADOS, `estudios/upbit_ia_o_rapido.py` |
| 5-nov | **Tamaño de los listados de Upbit** (31, 10.000 órdenes al azar) | 3 % al stop: 100 USDT → 297 (peor 5 %: 185), 10 % de chance de tocar el freno de 8 %; 5 %: 50 %; 1x: 97 % | 3 % (L-3); más, con ~30 operaciones reales | ESTUDIO-LISTADOS, `estudios/upbit_tamano.py` |
| 5-nov | **Otros avisos de Upbit al instante** | fin del soporte corto (9): mediana +4,75 %; advertencia corto (18): +1,73 %, 78 %; extendida y levantada, no | candidatos (L-9): se deciden con ~30 casos | ESTUDIO-LISTADOS, `estudios/upbit_primero.py` |
| 5-nov | **Binance: nada se cancela antes de 5 s** | el stop que sobra se cancela en segundo plano a los 5,5 s (pedido del dueño) | hecho (D-6, L-7) | `execution/live/binance_venue.py` |
| 6-nov | **Prueba de punta a punta en Tokio** (`deploy/tokio/prueba_listado.py`, agente aparte, avisos armados a mano) | Al principio: el primer aviso esperó 1,1 s (cargar el SDK de la IA frenaba el bucle) y el segundo listado del día se perdía (la deduplicación juntaba dos avisos de Upbit con la misma plantilla: 0,62 > 0,6). Arreglado: cada aviso entra a los 23-60 ms de recibido (~1,07 s desde la hora del mensaje) | hecho (precarga de la IA, deduplicación por tickers, tiempos por etapa en el registro) | `live.py`, `ingestion/dedup.py`, `intelligence/llm.py` |
| 6-nov | **Reinicio con swap y actualizaciones a las 15:00 UTC** (el dueño) | todo volvió en 10 s; prueba de punta a punta con `main`: órdenes a los 25-68 ms (SOL 68, ETH 27, deslistado de DOGE 25), sin errores; 387-457 MB libres + 1 GB de swap | hecho | sección 11 |
| 6-nov | **Canal de anuncios de Binance en Tokio** | 3 avisos: TradFi programado a hora redonda (01:00:00), 41 s; "Recovery Update #9" (03:06:23 UTC, hora no redonda), 1,2 s de publicado a recibido y decisión en 4 ms; "Limited-Time Spot Taker Program" (6-nov 08:00:00 UTC, **hora redonda**), 2,6 s. La hora redonda no siempre llega tarde | medir con más avisos (sobre todo deslistados) | sección "Vigilancia" |
| 6-nov | **Memoria del agente en Tokio, por partes** (`deploy/tokio/memoria.sh`) | a las 1,7 h: arena principal de glibc 30 MB, 9 arenas de hilos 68 MB, objetos de Python 80 MB; la misma réplica en la PC (Windows) ~105 MB; el SDK de la IA solo, ~45 MB. Plano entre 11:23 y 11:38 UTC | `MALLOC_ARENA_MAX=2` (#66); comparar a las mismas horas | sección 11 |
| 6-nov | **Prueba de punta a punta en la PC con los tiempos de Tokio** (`prueba_listado.py --pc`, Binance falso a ~3 ms) | SOL, ETH y DOGE entran; orden a los 148 ms la primera (conexiones en frío; en Tokio, 68 ms) y a los 33-48 ms las demás; la IA confirma en 1,3-2,6 s; sin errores | la prueba de siempre, en la PC; en Tokio solo si cambia el servidor | sección "Vigilancia" |
| 6-nov | **Freno del bucle del agente en Tokio** (máximo de cada minuto del latido, cada 15 s) | normal 10-45 ms; 122 ms en la pasada de aprendizaje (rehacía la deduplicación en el bucle; en la PC 300-365 ms) y 60-95 ms cada 2 min con el tablero exportado (redactaba releyendo el entorno 1.008 veces) | la deduplicación en un hilo (en la PC, máximo 37-99 ms) y el entorno leído una vez (#66); medir otra vez en Tokio | sección 11 |
| 6-nov | **Conexión nueva contra abierta con Binance, desde Tokio** (`curl`) | nueva ~45 ms (TLS ~43), abierta ~1,8 ms; aiohttp cierra las ociosas a los 15 s | 4 conexiones tibias con un pedido liviano cada 10 s; medir la primera entrada con la prueba en Tokio | sección 11 |
| 6-nov | **Tokio con #66 a #69 (instalado 12:28 UTC), antes de armar dinero real** | prueba de punta a punta en Tokio, 2 corridas: la primera entrada (SOL) a los 50-57 ms (antes 68), ETH 21-29 ms, el corto de DOGE 24 ms; la IA confirma en 1,1-1,7 s; sin errores. En la 1.ª corrida el simulacro no entró en DOGE (la práctica sí) y no quedó el motivo: desde ahora la prueba imprime por qué no entró cada aviso. Memoria: 118-121 MB en los primeros 5 min, con 1 arena de hilos (8 MB; antes 9 arenas, 68-92 MB) | listo para que el dueño arme (sección 6); comparar la memoria a las mismas horas (1,7 h: antes 186 MB; 2,5 h: antes 200-216 MB) | sección 6 |
| 6-nov | **Dinero real armado en Tokio, solo listados de Upbit** (13:11 UTC, el dueño) | `binance-real-check` primero falló (-2013: el stop de prueba no se veía a los ~3 ms) y pasó con #71 (el stop apareció a los 1,08 s); armado `--estrategias listado_upbit`, todo el saldo, 30 días. Verificado: el ejecutor puede abrir, solo `listado_upbit`, saldo leído y suficiente, sin pausa ni errores | operando con dinero real; lo demás en práctica | sección 6 |
| 6-nov | **Práctica o simulacro para los demás módulos** (pregunta del dueño) | la práctica toma las mismas decisiones con un llenado genérico (80 ms supuestos); el simulacro es el código del dinero real con el libro real nivel por nivel. Tokio no está cargado (agente 1,4 % de CPU, carga 0,00-0,02, 366 MB libres + 1 GB de swap). En la PC cada pedido a Binance tarda ~320 ms (Tokio 2-5 ms): mediría peor los deslistados | en Tokio, sin práctica: simulacro junto al real que nunca compite (opción 1 del dueño, REGLAS v3.5) | sección 2 |
| 6-nov | **Simulacro junto al dinero real, instalado (16:28 UTC, #73)** | antes (práctica + real, a las 3,1 h): 157 MB, 1,1 % de CPU, carga 0,00; después (real + simulacro, sin práctica, al minuto): 121 MB (igual que al arrancar antes), 1 arena de hilos, carga 0,08, sin errores. La memoria del #66: 157 MB a las 3,1 h contra 200-218 MB antes, a las mismas horas | comparar a las mismas horas; revisar que el simulacro no tome ningún listado de Upbit (`la_opera_el_dinero_real`) | sección 2 |
| 6-nov | **Upbit: ¿salir antes de los 15 min?** (31, Telegram a 1 s, pedido del dueño) | 15 min (la regla): mediana +9,16 %, media +12,02 %, 71 %; 30-60 s: mediana +7,9 a +8,8 %, media +10,4 a +10,7 %, 74 %; 2-5 min: 1,5 a 1,8 puntos menos que 15 min (rango del 90 % debajo de 0); stop que sigue al pico de 3-8 %: media +5,0 a +6,6 % (el sacudón de los primeros segundos lo saca); mitad a 60 s y mitad a 15 min: +11,30 %. El techo: 21 de 31 en los primeros 30 s (mediana 15 s del aviso) y 9 entre los 5 y 15 min; a los 15 min devuelve 6 % del techo (mediana). Por mitades: 1.ª (hasta 5-ago) 45-60 s algo mejor; 2.ª, 15 min bastante mejor | sin cambio: se queda en 15 min | `estudios/upbit_salidas.py` |
| 6-nov | **Upbit: ¿se llena la orden IOC?** (31; libro a +25 ms, orden a +50 ms) | tope de 0,5 % (el de hoy): a 1 s de Telegram se llena 30 de 31 (mediana +8,40 %, media +12,26 %; por listado +11,86 %); con 10 %, 31 de 31 (+12,54 %); a 0,5 s y a 2 s, 31 de 31 con cualquier tope | sin cambio (0,5 %) | `estudios/upbit_tope_ioc.py` |
| 6-nov | **Resultados: filtrar por la guidance** (88, anexo 99 del 8-K en EDGAR) | la regla (60): mediana +0,93 %, media +1,17 %, 63 %; guidance a favor del precio (10): +1,47 %, 80 %; en contra (9): +0,55 %, 67 %; no dice o mantiene (41): +1,23 %, 59 % | sin cambio (todas positivas, muestras chicas) | `estudios/resultados_guia.py` |
| 6-nov | **Listados de Binance al instante, construidos** (REGLAS v3.6) | tests: entra a 1,2 s aunque el libro dispare B-4, no entra a 2,6 s ni sin hora de publicación ni si ya subió 13 %, se cierra si la IA no confirma, confirma aunque la IA diga 1000PEPE; las 66 monedas del estudio salen de la regla de títulos. Punta a punta en la PC con los tiempos de Tokio: orden a los 39 ms de recibido (1000PEPEUSDT), la IA confirma en 1,2 s, sin errores (la prueba usaba el mismo enlace para dos avisos y la deduplicación los juntaba: arreglado en la prueba) | la arma el dueño con dinero real (sección 6) | `decision/binance_listings.py`, `tests/test_binance_listings.py` |
| 6-nov | **Bithumb, OKX, Bybit y Coinbase en el perpetuo de Binance, al segundo** (pedido del dueño) | Bithumb: 84 monedas (sep-2025 a nov-2026, la historia sacada de su página con el navegador), 77 saltan; comprar a 1 s del aviso con la regla: mediana +4,29 %, media +7,09 %, 83 %; a 2 s +2,32 %; en 2026 la ventana es de ~1 s. OKX (4) y Bybit (9): no saltan, ~0 %. Coinbase: la apertura del mercado (su página de estado) no mueve (31, −0,4 %); el aviso real sale por X, paga. Acceso desde Tokio: API de avisos de Bithumb 130 ms (solo los últimos 5), OKX 76 ms, Bybit 86 ms, estado de Coinbase 30 ms; Upbit sigue dando 403 a AWS | Bithumb: candidato (pendiente 16); OKX, Bybit y Coinbase: no | `estudios/otros_listados_segundos.py`, ESTUDIO-LISTADOS |
| 6-nov | **Bithumb: por dónde llegar rápido** (pedido del dueño: "si te lo bloquea busca otras alternativas (ej telegram)") | API pública (`api.bithumb.com/v1/notices` → `feed-api`): Cloudflare con caché de 4 h que ignora los parámetros; canal oficial de Telegram: solo alertas de precio; ningún canal gratis repite sus altas; `gw.bithumb.com/feed/v2/customer-support/articles/notices/recent` (su web de trading): sin caché, 120 ms desde Tokio, sin bloqueo a un programa | medir las dos en Tokio 72 h (`estudios/bithumb_demora.py`) y decidir la regla con eso | pendiente 16 |
| 7-nov | **El tablero por módulo y los datos para mejorar** (pedido del dueño: "que el dashboard muestre los diferentes módulos [...] guardar la info que nos sirva para aplicar mejora continua [...] cuidando la memoria y performance") | Cada decisión guarda el aviso (publicado, recibido, demora del canal), los ms hasta la orden, el precio de referencia y los frenos usados; cada salida, el camino del precio (pico, piso, cuándo fue el pico y el retorno a 30 s, 1, 5 y 15 min, 1 y 4 h), con el libro que el operador ya mira cada segundo: sin pedidos ni escrituras de más. El agente escribe `state/modulos.json` (estado, fuente, parámetros del código y estudio de cada módulo) y el tablero muestra una tarjeta por módulo: lo que dijo el estudio contra lo que pasa en vivo en cada cuenta | en marcha | `research/modulos.py`, `dashboard.by_module`, `shadow.track` |
| 7-nov | **Hipótesis 1: corto por deslistado de Binance** (pedido del dueño: "a los ~40-100 ms de publicado"; 49 monedas desde feb-2025) | el precio cae entre los 3 y los 5 s: entrar a 0,1, 1,3 o 2 s da lo mismo. A 2 s, salida a las 4 h (la regla de hoy): mediana +17,96 %, media +13,43 %, 84 % (1.ª mitad +7,0 %, 2.ª +19,6 % de media); a los 60 s +7,50 %; stops que siguen al pico, +1,9 a +4,0 %. El piso llega a los ~78 min (mediana). La límite IOC con tope de 0,5 % se llena 33 de 49 a los 2 s (con 2 %, 43). El deslistado del propio perpetuo (20): primero sube, sin ventaja | ya es un módulo (`deslistado_corto`, en el simulacro); con plata lo decide el dueño; el tope de 0,5 % es del sobre (limits.yaml, suyo) | `estudios/hipotesis_direccionales.py` |
| 7-nov | **Hipótesis 2: largo en BNB por Launchpool** (30 desde 2024; 64 HODLer y 5 Megadrop para comparar) | BNB salta entre los 2 y los 3 s (+0,6 a +0,8 %). Entrando a 0,1-1,3 s: mediana +0,54 %, media +0,83 %, 77-80 % a los 45 s; a los 2 s, +0,27 % y +0,49 %; a los 2,7 s, nada. HODLer (BNB retroactivo): nada. Desde 2025 hubo 9 Launchpool (el último en mar-2026) | no: ventaja chica, rara y solo si se llega antes de los 2 s | `estudios/hipotesis_direccionales.py` |
| 7-nov | **Hipótesis 3: corto por advertencia de Upbit y Bithumb (DAXA)** (desde 2025) | Upbit (42): sin salto; a los 2 s, salida a las 4 h: mediana +2,02 %, media +2,77 %, 69 % (las dos mitades positivas); a los 45-60 s, +0,5-0,7 %; el piso llega a ~1,5 h. Bithumb (43): más flojo, a las 4 h +0,90 %, 53 %. Liquidez de sobra (0,5 % de tope se llena siempre). Su fin del soporte es más fuerte: Upbit (16) a los 5 min +4,59 %, 88 %; Bithumb (20) +1,33 %, media +3,47 %, 75 %. Levantada y prórroga: sin ventaja | candidatos: corto por advertencia de Upbit (salida 4 h) y por fin del soporte (Upbit y Bithumb); los decide el dueño (PR protegido) | `estudios/hipotesis_direccionales.py` |
| 7-nov | **Las reglas coreanas, como las opera el agente** (el dueño: "Sí, armalas") | Fin del soporte con el PRIMER aviso (Upbit o Bithumb) por moneda y día: 25 monedas, 11 anunciadas por los dos a la vez, y Bithumb publicó primero o junto en las 11 (0 a 6 s antes). Corto a los 2 s, salida a los 5 min: mediana +3,73 %, media +4,28 %, 80 % (mitades +5,1 / +3,5 %); a 1 s +5,03 %; a 5 s +3,59 %; a 10 s la mediana +0,60 %. Freno "ya cayó más de 6 %" desde la apertura del minuto: las que deja afuera rinden menos (−0,8 % a −2 % a los 2-5 s). Advertencia de Upbit: a los 30 s todavía +1,56 % de mediana (4 h); con el freno de 3 %, las que deja afuera perdían (−10 %, −6 %). Los 3 "cambio de fecha" (변경) no tenían perpetuo: excluirlos no cambia la muestra | `decision/korean_notices.py`: advertencia hasta 30 s y 3 %; fin del soporte hasta 5 s y 6 %, salida 5 min | `estudios/hipotesis_direccionales.py` (bloque 5) |
| 7-nov | **¿Cuánto ensanchar el tope de la orden?** (el dueño: "tenes autorizacion para ensanchar") | Con la límite IOC y la orden a los 2 s, por aviso (las no llenadas, 0): deslistado de Binance con su freno de 4 %, tope 0,5 % → 33 de 45 (+7,23 %), 1 % → 40 (+10,77 %), 2 % → 43 (+12,27 %), 3 % → 44 (+12,48 %): las que solo entran con más tope son las caídas más rápidas (+15 a +36 %). A 1,3 s: 0,5 % → 38, 2 % → 43. Fin del soporte: 0,5 % → 21 de 24 (+4,54 %), 2 % → 23 (+4,47 %): no suma. Advertencia: 0,5 % → 42 de 42 | deslistado 2 % (`policy.EXITS`); lo demás 0,5 %; el sobre pasa a 200 pb de techo y una orden sin tope pedido va con 0,5 % (`safety.DEFAULT_SLIPPAGE_BPS`); REGLAS L-11 | `estudios/hipotesis_direccionales.py` (bloque 5) |
| 7-nov | **Las alarmas del libro en las caídas al instante** | No hay datos para medirlas: el archivo público de Binance dejó de guardar el mejor precio de los perpetuos en mar-2024 (`bookTicker`) y no hubo ningún deslistado desde que existe la regla (4-nov). El estudio mide entrar EN la caída (la primera operación después de la orden), así que mirarlas era apartarse de lo medido | sin alarmas en el deslistado, la vigilancia y el fin del soporte (como los listados al instante); quedan anotadas en `alarmas_del_libro` para medirlas en vivo (REGLAS B-4) | `research/shadow.py` |
| 7-nov | **El candado del operador con posiciones abiertas** | Prueba de la PC: con 4 posiciones abiertas, un listado de Upbit esperó 156 ms el candado: la vigilancia de cada segundo pedía los 4 libros de a uno con el candado tomado. Ahora los pide juntos y antes del candado: 5-6 ms | arreglado (`live.watch_once`); importa más ahora que los cortos de 4 h dejan posiciones abiertas | `deploy/tokio/prueba_listado.py --pc` |
| 7-nov | **Upbit: la dirección `emergency_popup/notice` que encontró el dueño** ("doble fuente") | Desde Tokio responde 200 en 91 ms, sin copia de Cloudflare, pero trae UN objeto: el cartel de emergencia del sitio (`active: false`, una imagen de mantenimiento), no los avisos de trading. La lista de avisos (`announcements?category=trade`) sigue en 403 desde AWS | no sirve como fuente; Upbit sigue por Telegram. Segunda fuente posible: la PC (donde el sitio responde) lee la lista cada 8 s y la manda a Tokio por el relé firmado (respaldo para las reglas lentas, no para los listados) | `deploy/tokio/prueba_api_upbit.py` |
| 7-nov | **Catálogos del CMS de Binance, fase 1** (pedido del dueño: 48, 161, 93, 157 y 49) | Una vuelta desde Tokio (sin 429): 29-53 ms por CloudFront de Tokio, pero SIEMPRE copias (13-97 s de edad; la dirección del relé, 97 s): por eso el canal principal es el WebSocket (~1,2 s, todos los catálogos, trae el número de catálogo). El pedido SIN catálogo trae los 7 juntos (48, 49, 93, 161, 157, 51 API Updates y 128 Crypto Airdrop). La etiqueta de vigilancia sale casi siempre en el 49 y los HODLer en el 128: hoy el respaldo del relé (solo el 48) no cubre deslistados, vigilancia ni HODLer | fase 2 (§10) | `deploy/tokio/test_binance_catalogs.py` |
| 7-nov | **Hipótesis: corto por etiqueta de vigilancia** ("sangría lenta", salida a la hora) | 53 monedas de 16 avisos desde mar-2025: NO es lenta, cae entre el segundo 2 y el 3 (−0,66 % a 2 s, −4,1 % a 3 s, −6,7 % a 15 min). Entrando a 1,2 s (nuestro WebSocket): 1 min +4,22 % (98 %), 5 min +5,07 % (98 %), 1 h +5,90 % (85 %), 4 h +6,58 % (87 %; mitades +5,6 y +8,1 %); a 2,6 s (hora redonda) 4 h +4,28 % (75 %). Piso en 15 min a los ~133 s (−9,6 %); en 4 h a los ~25 min. Freno de 2 % deja afuera 21 de 53 a 2,6 s; tope de 0,5 % llena 33 de 53 (por aviso +4,3 % contra +6,2 % con 2 %). Llegando tarde con el freno de 4 %, todavía gana: a 5, 10 y 30 s, mediana +4,2, +4,7 y +4,5 % (75 a 83 %) | REGLAS v3.8 (PR protegido): freno 4 % y tope 2 %; sigue en el simulacro, 1 % al stop; candidata a dinero real | `estudios/hipotesis_direccionales.py vigilancia` |
| 7-nov | **Hipótesis: largo por advertencia levantada** (Upbit 지정 해제, "impulso prolongado") | Upbit (8): entrando a 1 s, 1 min +2,22 % (88 %), 5 min +1,17 %, 1 h −0,01 % (50 %); entrando a 2 s, nada. Bithumb (17): 1 min +1,49 % (82 %), 1 h ~0. Lo mejor llega a los 15-30 s y a la hora se borró | no: es un salto de segundos, al borde de nuestra demora (Telegram ~1 s) | `estudios/hipotesis_direccionales.py advertencias` (caché) |
| 7-nov | **Advertencia de Upbit: designación formal contra pedido de precaución** (propia) | Designación (27): a 4 h media +3,85 %, 70 %; mitades +3,3 y +4,4 %. Pedido de precaución "유의 촉구" (15): a 4 h mediana +2,42 %, media +0,83 % (una pérdida grande), 67 %; mitades +0,6 y +1,1 % | la regla sigue con las dos (las dos ganan en las dos mitades); revisar con más casos | caché del estudio |
| 7-nov | **Hipótesis: alta en wones de Upbit contra listado nuevo** (¿ventana más limpia?) | Sí. Alta en wones (38 con perpetuo): a 1 s ya subió +0,3 %, a 2 s +15 %, a 10 s +31 % y se sostiene; entrando a 1 s, 1 min +16,97 % (76 %), 5 min +16,76 %, 1 h +16,61 % (82 %); a 3 s todavía +12,4 % a 15 min; a 5 s +7 %. Listado nuevo (94): entrando a 1 s +7,26 % a 1 min (70 %), a 2 s ~0 de mediana (media +4,9 %), a 3 s −1,8 %. Con el freno actual (+16 % desde la apertura del minuto), el alta en wones deja afuera 7-13 entradas buenas a 2-3 s; con +25 %, ninguna. Un caso (BABY, 5-jun-2026) es un error de datos (precios de dos escalas al abrir el perpetuo) | candidato: separar la regla de Upbit por tipo (freno de +25 % en el alta en wones; el listado nuevo solo muy rápido). Antes, cruzarlo con la hora de Telegram (un segundo de diferencia en la referencia lo cambia todo) | `estudios/hipotesis_direccionales.py listados_upbit` |
| 7-nov | **Módulos con números en vivo** | Solo "resultados de empresas" operó: 5 casos distintos (WEN, IONQ, MP, MRNA y DKNG; cada uno en la práctica y en el simulacro), ganó 1 (MP +2,8 %), un stop de −5,2 % (IONQ); el estudio decía 65 %. Los listados, deslistados, vigilancia y reglas coreanas: ningún aviso desde que se armaron. WEN es la acción de Wendy's (contrato TradFi), no la memecoin | alerta amarilla para resultados; revisar con ~15 casos (F-3 corta solo a los 30) | diarios de Tokio y de la PC |
| 7-nov | **Catálogos de Binance, fase 2: la lista sin catálogo, sin copia** (desde la PC, un pedido cada 30 s, L-7) | La lista SIN catálogo trae los 7 (de 5 a 50 avisos por catálogo; 15 KB con 10) y las dos direcciones (composite y apex) dan la misma respuesta. El orden de parámetros que usa la web de Binance (`type, pageNo, pageSize`) es SIEMPRE copia, en las dos direcciones (la piden otros: edad de 17 a 111 s); los otros 5 órdenes y todos los tamaños llegaron sin copia ("Miss"). La copia dura ~120 s | el relé intercala: una vuelta el 48 (sus 72 formas) y otra la lista sin catálogo (40 formas: 2 direcciones × 5 órdenes × 4 tamaños, sin el orden de la web). El 48 sigue cada 3 s, los demás catálogos quedan cada 6 s y cada forma vuelve a los 240 s o más: mismos pedidos por IP. Ahora el relé también respalda deslistados, vigilancia y HODLer si se corta el WebSocket | `estudios/binance_catalogos_cache.py`, `ingestion/catalog.py` |
| 7-nov | **Aviso de catálogo nuevo de Binance** (el "DefaultLoggingHandler" del plan) | El WebSocket trae el número de catálogo: si llega uno que no está en la lista (48, 49, 50, 51, 93, 128, 157 y 161), se avisa UNA vez por Telegram, sin sonido, después de entregar el anuncio (no le suma demora), y el anuncio sigue su camino como cualquiera | — | `ingestion/binance_ws.py`, `app.py` |
| 7-nov | **Upbit: ¿segunda fuente?** | Por la PC, no: la PC se puede apagar (el dueño, 7-nov: "tené en cuenta que la PC se puede apagar"), así que el respaldo faltaría justo cuando haga falta, y abriría una entrada nueva de avisos hacia el dinero real. Desde Tokio no se esquiva el bloqueo de Upbit a las IP de AWS. Queda su canal de Telegram, vigilado en el tablero y en `revisar.sh` | descartado | — |
| 7-nov | **Instalado v3.8 y la fase 2 de fuentes en Tokio** (#84 y #85, 15:21 UTC) | Sin posiciones reales; `revisar.sh` antes y después: todo OK (8 conexiones tibias, 14 de 14 fuentes, WebSocket conectado, 150 MB; el bucle marcó p99 12 ms recién arrancado y a los minutos 1,85 ms). El relé arrancó leyendo la lista sin catálogo (70 avisos de los 7 catálogos, sin errores) y Telegram se reconectó en 1,5 s | — | `deploy/tokio/instalar-agente.sh` |
| 7-nov | **Listados de Upbit con la hora REAL de Telegram** (antes de tocar una regla con dinero real) | 1.878 mensajes del canal desde may-2025 (la vista pública, una página cada 3 s): 146 de 180 avisos con su mensaje. Telegram sale 0 a 2 s después del sitio, casi siempre "+1 s" (en segundos enteros), y el agente entra ~0,2 a 1,2 s después de esa hora. Por aviso, a los 15 min: el **alta en wones** (27) +17,8 % entrando a 0,2 s, +12,6 % a 0,8 s y +10 % a 2 s; el **listado nuevo** (65) +5,7 %, +1,7 % y −0,3 %, con mediana negativa desde 0,6 s. Con freno de 25 % en vez de 16 %, +0,9 a +1,9 puntos por aviso en casi todas las demoras (los que ya subieron 16 % siguen subiendo). Los 92 a 0,7 s: hoy +4,9 % por aviso; con v3.9, +6,0 %. El estudio del 6-nov ("a 1 s de Telegram", mediana +9,16 %) suponía Telegram a 1 s del sitio: era optimista para el listado nuevo | REGLAS v3.9 (PR protegido, lo fusiona el dueño): freno de 25 % y el listado nuevo solo hasta 1 s del mensaje; el alta en wones, hasta 3 s | `estudios/upbit_telegram_cruce.py` |
| 7-nov | **La demora real de cada aviso de Upbit** | El diario del sistema de Tokio se borra al reiniciar (el reinicio de prueba de la mañana se llevó todo): no quedaba ninguna demora medida de Telegram a Tokio, que es justo lo que decide si un listado nuevo llega a tiempo | el relé anota cada aviso (hora del mensaje, llegada, salida) en `~/.trading/datos/telegram-upbit.jsonl`, después de reenviarlo; se instala junto con v3.9 | `relay/server.py` |
| 7-nov | **Revisión de Tokio y tablero simple** (pedidos del dueño: un procedimiento que siempre revise las conexiones tibias antes de instalar; que todo arranque solo; el tablero en una pantalla) | `deploy/tokio/revisar.sh` (solo lee) dio todo OK en Tokio con el dinero real recién armado (las 5 estrategias): 8 conexiones tibias, Telegram escuchando, 14 fuentes, 0 errores. El arranque solo ya estaba (servicios habilitados, esperan la red, se relanzan cada 5-10 s sin límite; probado en el reinicio del 6-nov). El tablero: la página son 34 KB armados en 12 ms y el supervisor usa ~27 MB; lo que sobraba era recargarla entera cada 5 s cuando Tokio manda datos cada 1-2 min. La lista de anuncios de Binance del relé devuelve HTTP 429 unas 6 veces por día (dos fallas y vuelve sola; el canal principal es el WebSocket): conocido, sin efecto | el instalador se niega con posiciones reales abiertas y corre la revisión al final; pantalla simple en "/" (30 s), detalle en `/detalle` (15 s) | `deploy/tokio/revisar.sh`, `dashboard.simple_page` |
| 7-nov | **¿Los cortos de 4 h le quitan lugar a los listados?** (el sobre deja 4 posiciones) | Con los avisos del estudio desde 2025 (49 deslistados, 43 advertencias y 25 fines del soporte con perpetuo; 291 avisos de listado de Upbit y Binance): al llegar cada listado había 0 posiciones abiertas en 267, 1 en 20, 2 en 1, 3 en 2 y 4 en 1 (13-may-2026, un deslistado de 4 monedas). Limitar los cortos a 3 a la vez lo evitaba, pero dejaba afuera 18 de 117 cortos (a 2, 42) | sin límite aparte: el cupo de 4 alcanza | cálculo con la caché del estudio |
| 7-nov | **Instalado v3.7 en Tokio** (#80, 09:13 UTC) | Sin posiciones reales. 14 fuentes funcionando (Bithumb registró sus 5 avisos como historia); módulos: los dos listados en dinero real, el resto en simulacro. Prueba de punta a punta en Tokio con el Binance real: SOL 69 ms (en frío), DOGE 27 ms, XRP 24 ms, ADA 23 ms de recibido (350 ms de publicado, por el lector de Bithumb), ETH y PEPE frenados solo por el sobre; IA 0,97-1,5 s; sin errores; memoria libre igual antes y después (403 MB). Conexiones tibias: 8 abiertas con Binance (AWS Tokio), 4 del dinero real y 4 del simulacro (`ss -tnp state established`, el pid de `agente-tokio`). Memoria del agente antes de reinstalar: 181 MB a las 4,8 h (157 a las 0,7 h: ~6 MB/h); después, 151 MB; bucle p99 2 ms | falta que el dueño arme las estrategias nuevas (sección 6) | `deploy/tokio/prueba_listado.py` |
| 7-nov | **Pruebas para el dinero real de los cortos** | (1) Prueba de punta a punta en la PC (Binance falso a ~3 ms y un Bithumb falso leído por el lector de verdad): DOGE 35 ms, XRP (advertencia) 44 ms, ADA (fin del soporte, leído a <1 s de publicado) 48 ms; la IA confirmó los títulos coreanos en 1,1-1,2 s; ETH y PEPE frenaron solo por el sobre; sin errores. (2) Ida y vuelta de un corto en la DEMO de Binance con el venue del dinero real (XRPUSDT, 20 USDT ficticios): venta IOC con límite exacto −2,00 % bajo el mejor comprador, llenada al mejor comprador; posición −16,9; stop de emergencia STOP_MARKET de compra al +30,0 % de cierre total; salida "solo reducir" llenada a los 6,5 s; sin posición ni stop al final. (3) `binance-real-check --demo`: todo OK, con el paso nuevo de la venta en corto con tope de 2 % | listo para que el dueño arme (sección 6); falta `binance-real-check` en Tokio con la clave real (lo corre el dueño) | `deploy/tokio/prueba_listado.py --pc`, `deploy/demo_corto.py` |
| 6-nov | **Binance: ¿el perpetuo va 10 s detrás del listado?** (pedido del dueño; 66 monedas de 58 avisos con perpetuo, ene-2025 a oct-2026, operación por operación) | no hay 10 s ni es en la apertura del spot (ahí el perpetuo no sigue al spot: comprar pierde, mediana −1,6 %): es en el AVISO. El perpetuo salta a los 2,30 s de publicado (mediana; mitad central 2,15-2,58 s); a los 2 s, nada. Comprar a los 2 s con la regla de Upbit (stop 10 %, 15 min): mediana +7,50 %, media +18,58 %, 77 % (1.ª mitad +13,8 %, 2.ª +4,1 %); a los 2,5 s +2,0 %; a los 3,5 s (esperando a la IA) −0,5 %. El canal llega a Tokio a 1,2-1,8 s (5 avisos; 2,6 s uno a hora redonda) | candidato a regla al instante como la de Upbit, con freno de demora; lo decide el dueño (PR protegido) y se mide primero en el simulacro | `estudios/binance_listados_segundos.py` |
| 5-nov | **Agente completo en Tokio (simulacro)** | Claude 1,8 s; Binance 2-5 ms (PC ~320 ms); fuentes OK salvo el sitio de Upbit (403); memoria: agente 77 MB y relé 63 MB de 416 | andando (`agente-tokio`); telemetría al tablero de la PC; la pausa de la PC frena a Tokio | RELE-TOKIO |
| 5-nov | **1.ª operación del simulacro** | Wendy's, resultados: entró a los 120 s, salió a los 30 min, −0,27 USDT (−0,5 %); su comunicado salió minutos antes del 8-K | normal (pierden ~35 %) | simulacro |
