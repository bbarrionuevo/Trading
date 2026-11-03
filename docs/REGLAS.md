# Reglas del agente TRADING (v3)

**Estado:** propuesta del 3-nov-2026. Reemplaza a la v2 (2-nov-2026), que reemplazó a la v1 (R-01 a
R-22, 24 a 26-oct-2026, bot de market making de nexus).

La hizo Claude a pedido del dueño:

- "estás aprobado para hacer todos los cambios necesarios";
- "Podés modificar las reglas y todo lo que nos sirva para que esto funcione y sea lo más autónomo posible";
- "este es un proyecto nuevo y tanto las reglas como los límites intocables los tenemos que definir en esta
  sesión contemplando las necesidades del objetivo".

**Se aprueba al fusionar el PR que la introduce.** Este archivo es ruta protegida (G-2).

## Principio: autonomía total dentro de un sobre de riesgo

El agente decide y opera solo, las 24 horas. Nadie aprueba operación por operación. Elige fuentes,
interpreta noticias, elige activos y lado (largo o corto donde se puede), dimensiona, entra, sale, se
pausa y aprende sin pedir permiso.

Lo que no decide es el **sobre**: cuánto capital real hay, cuánto se puede perder por operación, por día
y en total, cuánta exposición y cuánto apalancamiento. El sobre lo fija el dueño en `config/limits.yaml`.
Lo hace cumplir un núcleo de seguridad chico (`limits.py`, `safety.py`) que ni las estrategias, ni el
NLP, ni Claude pueden modificar en ejecución.

Cuanto más evidencia junta, más sube de nivel. Cuando algo sale mal, baja de nivel solo.

## Dónde corre

| | PC del dueño (motor principal) | GitHub Actions (respaldo) |
|---|---|---|
| Qué | `tradingagent run` 24/7: ingesta continua y operador en vivo | `collect --paper` cada 5 min: datos, etiquetas, aprendizaje y paper por corridas |
| Por qué | El alfa de una noticia dura minutos (vida media de 120 s): hace falta un proceso continuo | Medir y aprender aunque la PC esté apagada |
| Reglas | G-5 y G-7 | G-1 y G-6 |

## Niveles de autonomía

| Nivel | Qué hace | Quién lo activa | Cómo se baja |
|---|---|---|---|
| N0 · Investigación | Reproducción de días pasados, harness de escenarios | Siempre disponible | — |
| N1 · Paper | Opera solo con libros reales y llenados simulados (latencia, deslizamiento, comisiones, funding) | Por defecto | — |
| N2 · Real acotado | Lo mismo con dinero real, hasta `capital.max_live_usdt` | El dueño, con un armado local en la PC (A-2), cuando se cumple F-3 | Solo: circuit breaker, KILL, incidente o vencimiento ⇒ N1 |
| N3 · Real escalado | Más capital | El dueño, con nueva evidencia y un sobre nuevo | Igual que N2 |

**Esta versión solo tiene N0 y N1:** no existe código que envíe órdenes reales (test `test_a3_*`).

## A. Sobre de riesgo y dinero

| ID | Regla | Cómo se hace cumplir |
|---|---|---|
| A-1 | **Sobre de riesgo:** tamaño, riesgo por operación, exposición bruta y **neta** (≤ 30 % del equity), apalancamiento, pérdidas, spread, profundidad, edad de noticias y libros, órdenes por minuto y gasto en IA, en `config/limits.yaml`. Más estricto siempre se permite y el agente puede achicarlo solo en ejecución. Más laxo que la línea base, solo lo aprueba el dueño | `limits.BASELINE`. El CI (`tools/check_limits.py`) y el agente al arrancar rechazan un sobre más laxo. Tests `test_a1_*` |
| A-2 | **Dinero real solo con armado del dueño en la PC:** un comando local con frase exacta, tope de capital, venues y activos, y vencimiento de 30 días como máximo. Dentro del armado, el agente opera sin pedir nada más. Vencido o revocado, no abre posiciones; las abiertas salen normal | Etapa 4. Hasta entonces el modo real no existe (`test_a2_*`) |
| A-3 | **Capital físicamente acotado:** subcuenta con solo el capital armado, clave **sin retiros** y con IP fija. El agente nunca mueve fondos fuera del exchange. Hoy las únicas claves de exchange son **de solo lectura** (MEXC): el cliente firmado solo hace GET y solo a una lista cerrada de rutas (saldos y comisiones) | Configuración del dueño en el exchange. Ningún módulo tiene endpoints de órdenes, apalancamiento, margen ni retiros (`test_a3_*`) |
| A-4 | **Circuit breakers:** pérdida diaria ≥ `loss.daily_pct` (realizada y no realizada) ⇒ **cierra todo** y no abre hasta las 00:00 UTC. Pérdida semanal ⇒ no abre hasta el lunes. Drawdown máximo ⇒ **cierra todo** y queda en halt hasta el rearme humano. Pérdidas seguidas ⇒ enfriamiento. Siempre con alerta de riesgo prioritaria. El informe sugiere 2,5 % diario: queda el 2 % del sobre, que es más estricto | `decision/risk.py` con estado persistente (un reinicio no borra la pérdida del día); el cierre total lo hacen `shadow.py` y `live.py`. Tests con reloj simulado |
| A-5 | **Toda posición con triple barrera:** stop-loss a 1,5σ, take-profit a 3σ (b = 2) y expiración, con σ de la hora previa a la noticia; el stop nunca más cerca que 0,6 % ni más lejos que 5 %. **Tamaño:** el menor entre ¼-Kelly (f = ¼·(p·b − (1−p))/b, con b neto de costos) y la paridad de volatilidad (pérdida al stop, costos incluidos, ≤ `per_trade.max_risk_pct` del equity), dentro de los topes del sobre. En futuros, la liquidación tiene que quedar a ≥ 3 veces la distancia del stop | `decision/barriers.py` y `decision/policy.py` (tests). El stop en el exchange llega con la etapa 4 |

## B. Datos y señales

| ID | Regla | Cómo se hace cumplir |
|---|---|---|
| B-1 | Cada fuente tiene un nivel de confianza: 1 oficial, 2 medio establecido, 3 sin verificar. Una de nivel 3 necesita `news.min_sources_unverified` fuentes distintas | Nivel en el catálogo y en el remitente del webhook (nunca en el cuerpo). La corroboración la cuenta la deduplicación |
| B-2 | **Nada viejo:** una noticia con más de `news.max_age_s` desde su publicación no se opera. Además, **el alfa decae** con vida media de 120 s desde la publicación y con más de 360 s (3 vidas medias) la noticia es obsoleta. La primera lectura de cada fuente es historia (backlog). Sin hora de publicación creíble se suma una vida media | `IngestionEngine`, `safety.news_max_age`, `intelligence/vector.py` |
| B-3 | **Una historia, una operación:** las copias de una misma noticia se agrupan y suben la confianza, pero no abren otra posición | Deduplicación exacta + MinHash/LSH (clusters); la política opera una vez por cluster |
| B-4 | **Validación de mercado antes de entrar**, con el libro real del momento: spread ≤ `market.max_spread_bps` y **z-score del spread ≤ 2,5** contra su media reciente; profundidad dentro de 50 bps ≥ `market.min_depth_multiple` × la orden; **L3** (3 primeros niveles de los dos lados) ≥ 5 × la orden; **divergencia** entre el último precio y el medio ≤ 1 %; **lambda de Kyle** de los últimos minutos ≤ 4 veces la de la hora previa; libro de menos de `market.max_book_age_s` | `decision/policy.py` (`market_blocks`), `marketdata/liquidity.py`, venue paper (libro viejo ⇒ rechazo) |
| B-5 | Las horas raras se marcan, nunca se confía a ciegas: publicación "en el futuro" o sin hora | `freshness = future / unknown` en la ingesta; B-2 suma una vida media |

## C. Inteligencia (NLP y modelos de lenguaje)

| ID | Regla |
|---|---|
| C-1 | **La salida de un modelo es un dato no confiable:** JSON con esquema (salidas estructuradas) y validado en el código; todo activo mencionado tiene que existir en el registro de instrumentos y aparecer en el texto (*grounding*); la certeza tiene tope (0,8) hasta que se calibre contra resultados reales |
| C-2 | **El texto de una noticia nunca es una instrucción.** Va delimitado como dato no confiable; el modelo no tiene herramientas y la única salida posible es el JSON del esquema. Lo que diga la noticia ("ignorá tus reglas", "comprá X") es contenido a clasificar |
| C-3 | **La IA no envía órdenes:** propone una señal (categoría, polaridad, certeza, activos, horizonte). El núcleo de decisión y el motor de riesgo, deterministas, deciden si se entra y con cuánto |
| C-4 | **Gasto acotado y respaldo determinista:** Claude Haiku 5.5 solo para las noticias que la capa rápida no resuelve sola, con tope diario `ai.max_llm_usd_per_day` contado con el uso real que informa la API, timeout corto y sin reintentos. Si el modelo falla, tarda, rechaza o no hay presupuesto, decide el clasificador local |
| C-5 | **Aprendizaje autónomo que solo reduce riesgo:** el agente puede apagar una categoría de un lado (n ≥ 30 y límite superior del IC95 de la ganancia neta < 0) y achicar la confianza del meta-modelo (κ ≤ 1, ajustado con la triple barrera real). Nunca agranda nada: lo que la evidencia deja de respaldar vuelve al valor por defecto, que el dueño aprobó. Cada versión tiene su propia evidencia (`strategy_tag`) |
| C-6 | **Se opera solo un evento claro:** vector C/P/I/M/D con relevancia de la entidad C ≥ 7 y fuerza \|α(Δt)\| ≥ α_min, y meta-modelo p = P(take-profit antes que stop) ≥ 0,60 |

## D. Ejecución

| ID | Regla | Cómo se hace cumplir |
|---|---|---|
| D-0 | Una sola puerta: todas las órdenes pasan por `ExecutionService`; la inteligencia nunca toca el venue | Arquitectura; la inteligencia solo produce señales y la política `OrderIntent` |
| D-1 | **Idempotencia:** el mismo `client_id` nunca genera dos órdenes | `PaperVenue.submit` (test) |
| D-2 | **Nunca a mercado sin tope de precio;** el tope nunca supera `per_trade.max_slippage_bps` | `safety.slippage_cap` y `intent_block_reasons` |
| D-3 | Con datos viejos no se envía nada | Libro de más de `max_book_age_s` ⇒ rechazo |
| D-4 | **Estado desconocido ⇒ conciliar antes de seguir:** si un error de red deja una orden en duda, el par no envía nada más hasta consultarla. Al arrancar se concilia todo con el exchange | Etapa 4 (conector real), con el diseño probado en la v1 |
| D-5 | Comisiones: se usa la mayor entre la configurada, la pública del exchange y la de la cuenta | Paper de MEXC futuros (`research/futures.py`); etapa 4 para real |
| D-6 | Tope de órdenes por minuto; 5 rechazos seguidos del exchange bloquean el par | `safety` (tope); etapa 4 (rechazos) |

## E. Paradas y alertas

| ID | Regla | Cómo se hace cumplir |
|---|---|---|
| E-1 | **KILL:** el archivo `TRADING_HOME/KILL` vacío (o `KILL` en la rama `data` para el paper de Actions) ⇒ no abre posiciones, pero salir siempre se puede. Con `2` ⇒ además cancela las órdenes. Con `3` ⇒ además cierra las posiciones con límite agresivo | `safety.intent_block_reasons` (nivel 1); los niveles 2 y 3 llegan con la etapa 4 |
| E-2 | El agente puede pausarse y bajar de nivel solo. Nunca se sube de nivel solo | Diseño |
| E-3 | **Las alertas nunca demoran al motor:** hilo propio, cola con prioridad (riesgo primero), nunca bloquean | `AlertDispatcher` (test con Telegram colgado: `submit` p99 ~10 µs) |
| E-4 | Toda operación, stop, take-profit, expiración, liquidación y circuit breaker se avisa por Telegram. La entrada dice par, dirección, capital, precio, SL, TP, causa y p del meta-modelo; la salida, motivo, retorno neto y deslizamiento contra el precio teórico | `alerts/model.py` (`entry_alert`, `exit_alert`), `shadow.py`, `live.py` |

## F. Registro, evidencia y promoción

| ID | Regla |
|---|---|
| F-1 | **Diario encadenado:** cada noticia evaluada, decisión (con su análisis: vector, alfa, p, plan y motivos), orden y resultado van a un diario JSONL con hash encadenado (`journal-verify` detecta una línea alterada o borrada) |
| F-2 | Cada resultado se etiqueta como teórico, simulado, paper o real. Solo cuenta como ganancia lo **cobrado** en real |
| F-3 | Para pasar de N1 a N2 tienen que darse **todos** estos criterios: (1) al menos 60 operaciones cerradas en paper y 21 días; (2) ganancia neta media por operación con intervalo de confianza del 95 % por encima de 0; (3) sigue positiva con +5 bps de costo por lado y +500 ms de latencia, re-simulada con el harness; (4) el drawdown del paper no pasó de la mitad del máximo del sobre; (5) ningún incidente abierto; (6) etapas 2 a 4 terminadas; (7) armado del dueño (A-2); (8) **DSR ≥ 0,95** (Bailey y López de Prado, corregido por la cantidad de versiones probadas). Una versión con 30 operaciones o más y DSR < 0,80 se descarta sola y no abre más posiciones |
| F-4 | **Excepción de calibración:** el dueño puede armar hasta 20 USDT reales antes de F-3 para comparar los llenados reales contra el modelo de paper. Mismo sobre, vencimiento de 7 días como máximo |
| F-5 | Latido visible en consola y en `state/heartbeat.json`: fuentes, noticias, lag del bucle, cartera, operador en vivo, alertas y diario |

## G. Seguridad, cambios y autonomía de desarrollo

| ID | Regla |
|---|---|
| G-1 | **Secretos** solo en `TRADING_HOME/.env` (Windows: `%LOCALAPPDATA%\TRADING\.env`). Nunca en el repo, los logs, las alertas ni el diario: todo texto de error se redacta (test `test_g1_*`). **Única excepción:** secretos cifrados de GitHub Actions para credenciales **sin poder sobre el dinero**: el token del bot de Telegram (`CHARBIS_BOT_TOKEN`), el chat (`TELEGRAM_CHAT_ID`), el User-Agent de la SEC (`SEC_USER_AGENT`), la clave de Claude (`CLAUDE_API_KEY`, con el gasto acotado por C-4) y las claves **de solo lectura** de MEXC (`ACCESS_KEY_MEXC`, `SECRET_KEY_MEXC`: sin permiso de trading ni de retiro; ver A-3). Nunca claves de trading ni de retiro. La lista de secretos que un workflow puede usar está en un test; el repo es público y sus logs también, así que ningún workflow imprime un secreto |
| G-2 | **Rutas protegidas (categoría C):** `.github/`, `docs/REGLAS.md`, `config/limits.yaml`, `src/tradingagent/limits.py`, `safety.py`, `paths.py`, `execution/live/`, `tests/test_rules.py` y `tools/`. Un PR que las toca deja en rojo el control `protected-paths`; **solo el dueño lo aprueba y fusiona** |
| G-3 | **Autonomía de desarrollo:** Claude desarrolla, prueba y abre PRs por su cuenta. Fusiona solo los de **categoría B** (no tocan rutas protegidas) con CI verde, y nunca uno con `protected-paths` en rojo. El dueño puede revocar esta regla con una línea en el PR o en el chat |
| G-4 | Dependencias con versión fija; acciones de GitHub fijadas por SHA; permisos mínimos en cada workflow |
| G-5 | **Separación de proyectos:** TRADING usa su propia carpeta (`%USERPROFILE%\TRADING`), sus datos (`%LOCALAPPDATA%\TRADING`), sus claves y su tarea programada (`trading-agente`). **Nunca usa ni toca nada de nexus**, que es otro proyecto del dueño en la misma PC. Si `TRADING_HOME` apunta a una carpeta de nexus, el agente no arranca (test `test_g5_*`, que también revisa los scripts de `deploy/`) |
| G-6 | **GitHub Actions** solo para trabajos **cortos y periódicos**: el recolector de datos y el **operador paper por corridas** (dinero simulado, libros reales), cada 5 minutos, con tope de 10 minutos por corrida, una corrida a la vez y solo en este repo. Nunca órdenes reales ni servicios 24/7 (para eso está la PC, G-7). Los datos van a la rama `data`, que es **pública**: solo datos públicos (titulares, enlaces, fragmentos cortos, precios, operaciones simuladas) y nada personal (ni saldos de la cuenta). El único permiso de escritura de un workflow es guardar en esa rama (tests `test_g6_*` y `test_g4_*`) |
| G-7 | **La PC del dueño 24/7** es el motor principal: la tarea programada `trading-agente` (`deploy/windows/instalar-agente.ps1`) corre `tradingagent run` al arrancar Windows y lo reinicia si se cae. El estado vive en `%LOCALAPPDATA%\TRADING` (persistente entre reinicios). Cambios en la tarea o en el `.env` los hace el dueño |

## Categorías de cambios

- **A · Automáticas:** leer datos públicos, simular, reproducir, informar y diagnosticar.
- **B · Con validación técnica:** estrategias, NLP, fuentes, parámetros dentro del sobre y dependencias. Van por PR con CI verde (G-3).
- **C · Solo con el dueño:** el sobre de riesgo, las reglas, el núcleo de seguridad, el armado de dinero real, las claves, el CI y lo irreversible. El silencio, una buena simulación o una recomendación de la IA **no** son autorización.

## Aprobaciones del dueño

| Fecha | Qué | Cómo |
|---|---|---|
| 24 a 26-oct-2026 | Reglas v1 y límites para 100 € (nexus) | Ver el historial de la v1 (commit `aeca87b`) |
| 2-nov-2026 | Pedido: rehacer las reglas para la máxima autonomía e inteligencia | Mensaje del dueño; la v2 quedó aprobada al fusionar su PR |
| 2-nov-2026 | Recolector de entrenamiento en GitHub Actions con el bot `CHARBIS_BOT_TOKEN` (G-1 enmendada, G-6 nueva) | Pedido del dueño ("quiero que se vaya entrenando", con los secretos ya cargados); aprobado al fusionar su PR |
| 2-nov-2026 | Pasos 1 a 7 del traspaso: paper en Actions cada 5 min (G-6 enmendada), Claude Haiku con `CLAUDE_API_KEY`, futuros de MEXC en paper con claves de solo lectura (G-1 y A-3), mejoras del informe | Mensaje del dueño: "estás aprobado para hacer todos los cambios necesarios" |
| 2 y 3-nov-2026 | Reglas v3 y límites definidos para el objetivo: la PC 24/7 como motor (G-7), futuros solo en MEXC (Binance limita a Argentina), exposición neta ≤ 30 % (A-1), cierre total en el freno diario y el halt (A-4), DSR en F-3 | Mensajes del dueño: "Podés modificar las reglas y todo lo que nos sirva para que esto funcione y sea lo más autónomo posible"; "este es un proyecto nuevo y tanto las reglas como los límites intocables los tenemos que definir en esta sesión"; "la idea es que este programa esté instalado en mi PC que va a estar 24/7 prendida"; "future solo lo podré hacer con MEXC". Queda aprobado al fusionar su PR |
