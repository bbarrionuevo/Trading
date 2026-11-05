# Plan por etapas

Cada etapa se cierra con criterios verificables (tests, métricas), no con "está hecho".

## Etapa 1 · Ingesta + paper trading + Telegram ✅ (2-nov-2026)

- **Ingesta:**
  - RSS/Atom seguro: defusedxml, tope de tamaño y de ítems.
  - Sondeo con GET condicional, backoff con jitter, `Retry-After` y circuit breaker por fuente.
  - Webhook firmado con HMAC y reproducción de noticias grabadas.
  - Deduplicación exacta y casi-duplicados (MinHash/LSH), con clusters y corroboración.
  - Filtro de ruido auditable.
  - Frescura (staleness), con la historia de la primera lectura sin operar.
- **Paper:**
  - Libro L2 en Decimal y latencia lognormal: se llena con el libro de llegada.
  - Recorrido del libro con participación parcial y tope de precio obligatorio.
  - Límites que se llenan solo si los atraviesan; stops re-preciados hasta salir; OCO.
  - Tick, lote y mínimo nominal; idempotencia; saldo spot y margen perp.
  - Cartera spot y perp; harness de escenarios.
- **Telegram:**
  - Despachador en hilo propio con prioridades, 429 y 5xx, y token redactado.
  - Mensaje de prueba al iniciar.
- **Telemetría:** diario con hash encadenado, latido, logs JSON redactados.
- **Reglas v2:** sobre de riesgo validado, núcleo de seguridad, rutas protegidas, CI.

**Criterios cumplidos:**

- 84 tests en Python 3.11 y 3.13, entre ellos uno de punta a punta con RSS y Telegram de prueba.
- Con Telegram colgado, `submit` da p99 ≈ 10 µs, igual que sin el hilo de alertas.
- **Pendiente en la PC:** enviar el mensaje de prueba con tu bot real y leer las fuentes reales
  (`sources --check`). Desde la nube, esos hosts están bloqueados por la política de red.

## Etapa 2 · Inteligencia (NLP y eventos) — en marcha (3-nov-2026)

**Hecho:** capa rápida determinista con certeza (`intelligence/classifier.py`), capa lenta con Claude
Haiku 5.5 solo para lo dudoso (`llm.py`: esquema, grounding, presupuesto, sin reintentos), vector de
evento C/P/I/M/D con decaimiento del alfa (`vector.py`) y meta-modelo calibrable que solo baja
(`meta.py`). El recolector guarda `nlp` y `nlp_llm` de cada noticia y la triple barrera de cada reacción.
**Falta:** el conjunto etiquetado a mano para medir la exactitud (≥ 85 %) y calibrar la confianza con
semanas de datos.

Para cada noticia aceptada, el pipeline:

- **Extrae** entidades y activos.
- **Clasifica** categoría (macro, regulatorio, hack, listado/delisting, earnings, ETF, aprobación,
  demanda, quiebra...), polaridad de −1 a +1, convicción y horizonte.
- **Verifica el grounding:** todo activo tiene que estar en el texto y en el registro de instrumentos.
- **Pondera** por corroboración y nivel de la fuente.

En dos capas:

1. **Rápida, local y determinista (< 5 ms):** léxico y patrones por categoría para lo inequívoco
   ("exchange hacked", "SEC approves", "delisting"). Opera sola cuando la confianza es alta.
2. **Modelo de lenguaje (~1-2 s):** Claude con salida estructurada, validada por esquema, para lo ambiguo.
   Con presupuesto diario (C-4), respaldo y defensa contra inyección (C-2).

**Calibración:** cada clasificación se guarda con el movimiento real del precio a 1, 5, 15 y 60 minutos.
Así la "confianza" pasa a ser una probabilidad medida.

**Datos (desde el 2-nov-2026):** el recolector de GitHub Actions (`research/`, REGLAS G-6) junta cada
15 minutos las noticias, sus copias en otras fuentes y la reacción del precio. Todo queda en la rama
`data`. Con eso se arma el conjunto etiquetado, y se mide qué fuentes y categorías mueven el precio,
cuánto ya se movió antes de publicarse (`pre_10m`) y cuánto queda por capturar según la latencia
(`r_recv_*`). También se mide si el filtro de ruido descarta lo que no mueve nada.

**Criterios:**

- Exactitud de categoría ≥ 85% sobre un conjunto etiquetado de noticias reales.
- 0 activos inventados (grounding).
- Latencia p95 de la capa rápida < 5 ms.
- El modelo nunca recibe ni devuelve órdenes.

## Etapa 3 · Motor de decisión y riesgo — en marcha (3-nov-2026)

**Hecho:** `decision/policy.py` (noticia → evento → plan → libro), triple barrera por σ, ¼-Kelly con b neto
de costos y paridad de volatilidad, microestructura (spread z, L3, divergencia, Kyle), costos (raíz
cuadrada y Almgren-Chriss con urgencia), frenos con estado que cierran todo (diario y halt), PSR/DSR por
versión, operador paper por corridas (Binance spot y MEXC futuros, con funding y liquidación) y en vivo
para la PC (`live.py`). **Falta:** la reproducción de 30 días de noticias grabadas con informe de
resultado neto, y la evidencia de F-3 (60 operaciones, 21 días, DSR ≥ 0,95).

- **Señal a intención:** solo si convicción × impacto esperado supera el umbral y la noticia sigue fresca.
- **Validación de mercado (B-4):** spread, profundidad y movimiento ya ocurrido. Si el precio ya se
  movió más que el impacto esperado, no se compra el techo.
- **Tamaño** por riesgo al stop (A-5): `qty = equity × riesgo% / distancia_al_stop`. La distancia sale
  del ATR, con fracción de Kelly acotada por la convicción calibrada y topes del sobre.
- **Salidas:** stop y take-profit en bracket (OCO) más salida por tiempo.
- **Circuit breakers (A-4)** con estado persistente: un reinicio no borra la pérdida del día.
  Una operación por cluster (B-3).

**Criterios:** tests de cada freno con reloj simulado; reproducción de 30 días de noticias grabadas con
informe de resultado neto y del costo de llegar tarde.

## Etapa 4 · Conector real (CEX)

- Un venue real detrás de la misma interfaz que el paper: Binance o MEXC spot primero, luego perpetuos.
- **Conciliación de estado desconocido (D-4):** al arrancar, cada 10 minutos y ante errores de red.
- **Órdenes:** IDs propios guardados antes de enviar y stops en el exchange.
- **Exchange:** comisiones verificadas, filtros de exchangeInfo, límites de API y KILL 2/3.
- **Armado local del dueño (A-2).** Va en `execution/live/`, ruta protegida.

**Criterios:** en testnet o sandbox, cada camino de error (timeout al enviar, rechazo, parcial,
reconexión) termina en un estado conciliado; calibración con hasta 20 USDT (F-4).

## Etapa 5 · Tablero y operación

- **Tablero local** (127.0.0.1) de solo lectura: noticias, decisiones, posiciones, PnL y salud.
- **Comandos de Telegram** que solo reducen riesgo: `/estado`, `/pausa`, `/kill`.
- **Informe diario** por Telegram.
- **Ejecución en la PC:** tarea programada `trading-agente`, nunca `nexus-*`.

## Etapa 6 · Promoción a dinero real

Cuando se cumple F-3: armado del dueño (N2), seguimiento diario de paper contra real y
retroceso automático a N1 ante cualquier divergencia.

## Plan de acción del 5-nov-2026: lo que sirve de los dos informes y de las cuatro ideas del dueño

Fuentes: "Arquitectura de Sistemas de Trading Cuantitativo Autónomo en Futuros de Criptomonedas" y "Arquitectura de
Sistemas HFT y Microestructura Cuantitativa" (los dos informes que pasó el dueño), más sus cuatro ideas (relevo en
Tokio, datos de la cadena, vacío del libro, foco en deslistados). Cada punto se contrastó con lo medido en la PC.

**Ya estaba hecho** (lo piden los informes y el sistema ya lo tiene): deduplicación MinHash/LSH; dos niveles (reglas
exactas al instante para los avisos con formato fijo, la IA para el resto); triple barrera con stop medido (listados
10 %); Kelly fraccional (¼); frenos de pérdida por día, semana y total; λ de Kyle y raíz cuadrada del impacto; tope de
precio en cada orden (IOC con tope); DSR para juzgar cada estrategia (sola, desde el 5-nov); estudios con la demora
real (sin mirar el futuro); deslistados y etiqueta de vigilancia de Binance (la idea 4).

**Hecho el 5-nov:** balde de fichas en el lector de libros (los dos informes): el camino interno de un aviso a la
orden bajó de ~220 ms a ~30 ms. La SEC ya no se corta minutos. Los deslistados piden todo en un solo viaje a Binance.

**Próximo, en orden y con evidencia:**
1. **Medir la demora de punta a punta** en el primer deslistado o etiqueta real (canal 1,2-1,8 s + agente ~0,03 s +
   orden). Si la entrada queda después de los 2,5 s: libro en la sombra por WebSocket (`!bookTicker` de todos los
   contratos) para decidir sin pedir nada por REST.
2. ✅ **Control de peso de la API de Binance** (5-nov, `marketdata/peso.py`): cada respuesta de Binance informa el
   peso usado de la IP; pasado el 80 %, el catálogo, los volúmenes y los intervalos de funding esperan al minuto
   siguiente (lo urgente nunca espera) y pasado el 90 % queda un aviso en el log. El latido muestra `peso_binance`.
3. ✅ **Segunda lectura de la IA en paralelo** (5-nov, `LiveTrader._ai_second_read`): después de entrar por un
   deslistado o una etiqueta de vigilancia (sin esperar a la IA), la IA lee el aviso; si dice que no es negativo
   para esa moneda, se cierra la posición (`la_ia_no_confirma`). Sin respuesta de la IA, queda lo medido.
4. ✅ **Registro fiscal** (5-nov, `python -m tradingagent informe-fiscal --cuenta real --anio 2026 [--csv archivo]`,
   `research/fiscal.py`): operaciones, ganadas, volumen operado, comisiones y resultado neto por mes de Argentina.
   Queda en la PC (nunca se publica).
5. **Upbit, fin del soporte:** 6 casos segundo a segundo, media ~+4 % con mucha dispersión (+19 % a −11 %). Seguir
   midiendo; con 15 casos, decidir. Las advertencias de Upbit no dan ventaja (n 13).

**Relé de Tokio (5-nov, pedido del dueño, `docs/RELE-TOKIO.md`):** instalado y andando (servicio `rele-tokio` en el
servidor; túnel firmado PC↔Tokio probado: 288 ms de ida y vuelta). Upbit bloquea las IP de AWS (403): el relé lee la
lista de anuncios de Binance como respaldo. Lo medido: el relé acelera lo que se sondea (Upbit, lista de Binance), no lo que Binance
empuja; la IA de segunda lectura va en la PC; `batchOrders` con stop no existe más (−4120 en la demo); el mínimo de 5
USDT no pide apalancamiento. Lo único que daría ~0,3 s en todo es operar desde Tokio (decisión del dueño, A-3).

**Necesita una decisión del dueño** (cuesta plata o hace falta una cuenta suya):
- **Servidor en Tokio** (idea 1, ~5 USD por mes): ahorraría ~0,2 s por aviso. Conviene si la medición del punto 1
  muestra que llegamos tarde por décimas. La cuenta la crea el dueño y me da acceso.
- Noticias pagas con "supera / no alcanza lo esperado" (Benzinga por Alpaca).

**Para investigar más adelante:** billeteras de Upbit en la cadena antes de un listado (idea 2); el vacío del libro
como alarma previa (idea 3).

**Descartado con datos o porque no aplica:**
- 6-K y 8-K que no son resultados: medido, sin ventaja. (Los 8-K de **resultados** sí la tienen: el primer informe
  generaliza de más.)
- Arbitraje de funding: riesgo de cola y necesita spot. Funding extremo como señal: medido, sin ventaja.
- Apalancamiento 2-5x para pasar el mínimo de 5 USDT: con 100 USDT, 1 % de riesgo y stop de 10 % ya dan 10 USDT.
- uvloop (no anda en Windows), batchOrders (los stops de Binance van por el servicio Algo, aparte), reescribir en
  NautilusTrader o Rust: el cuello es la red y el exchange, no Python (el agente decide en ~30 ms).
- Los "picos" de la tabla del primer informe (+33,6 % Upbit, +39,7 % futuros nuevos): se ven después, no son ganancias
  operables. Medido: ir largo en los futuros nuevos pierde.
- "Entrar a los 10-20 s": sirve en los listados de Upbit (calibrado), no en los deslistados ni en la etiqueta de
  Binance (a los 5-10 s ya no hay ventaja: la caída pasa entre los 2 y 5 s).
