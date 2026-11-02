# Arquitectura

```
 fuentes ──► INGESTA ──news.accepted──► INTELIGENCIA ──señal──► RIESGO ──OrderIntent──► EJECUCIÓN ──► venue
 (RSS,       normaliza · dedup ·         (etapa 2)               (etapa 3)  cola acotada   (paper hoy,
  webhook,   frescura · ruido                                               + núcleo de    real en la
  replay)        │                                                          seguridad      etapa 4)
                 └─news.rejected / news.corroboration                                         │
                                                                                     orders (bus)
   DIARIO encadenado ◄──────────── todo lo anterior ──────────────────────────────────────────┤
   (hilo propio)                                                                              │
   ALERTAS Telegram ◄──────────── trades, stops, TP, circuit breaker, inicio ─────────────────┘
   (hilo propio + bucle propio)
   LATIDO (consola + state/heartbeat.json) ◄── estado de todo, cada N s
```

## Módulos (`src/tradingagent/`)

| Módulo | Responsabilidad | Piezas |
|---|---|---|
| `core/` | Reloj inyectable, bus con colas acotadas, medidor de lag, eventos, redacción de secretos | `clock`, `bus`, `looplag`, `events`, `redact` |
| `ingestion/` | Fuentes y evaluación de cada noticia | `feeds` (parser seguro), `sources` (sondeo), `webhook`, `replay`, `normalize`, `dedup`, `noise`, `catalog`, `engine` |
| `marketdata/` | Libros L2 y proveedores (estático, grabado, REST público) | `book`, `providers` |
| `execution/` | Intenciones de orden, venue paper, cartera, servicio, harness | `orders`, `instruments`, `paper`, `portfolio`, `service`, `harness` |
| `alerts/` | Telegram fail-safe | `model`, `telegram`, `dispatcher` |
| `telemetry/` | Diario, latido, logs | `journal`, `heartbeat`, `logs` |
| `limits.py` · `safety.py` | **Sobre de riesgo y núcleo de seguridad** (rutas protegidas) | |
| `config.py` · `paths.py` · `app.py` · `__main__.py` | Configuración, carpeta privada, ensamblado, CLI | |

## Decisiones de diseño

- **Ejecución desacoplada:** la inteligencia nunca llama al venue. Produce `OrderIntent` y las encola en
  `ExecutionService.submit()`, que no bloquea. Cambiar paper por real no toca la inteligencia ni el riesgo.
- **Nada bloquea el bucle:**
  - toda la red es asíncrona (aiohttp, con timeout total por pedido);
  - el parseo de feeds corre en un hilo (`asyncio.to_thread`);
  - las alertas y el diario tienen hilos propios;
  - el latido escribe en un hilo.
  - `LoopLagMonitor` lo mide en vivo; en Windows el piso es de ~15 ms por la granularidad del temporizador.
- **Telegram fail-safe:** el despachador tiene su propio bucle en otro hilo. `submit()` es O(1), sin E/S.
  Si Telegram cae, se cuelga o limita, solo se atrasan las alertas. Medido con Telegram colgado:
  `submit` p99 ≈ 10 µs, igual que sin el hilo.
- **Dinero exacto:** precios, cantidades, comisiones y PnL en `Decimal`; tick, lote y mínimo nominal como
  en el exchange.
- **Paper riguroso, conservador a propósito:** con latencia, el llenado usa el libro de llegada. Solo cuenta
  una fracción de la profundidad visible. Los límites se llenan solo si los atraviesan. Ninguna orden
  sale sin tope. Un paper optimista enseña a perder plata en real.
- **Datos no confiables:**
  - XML con defusedxml y webhooks con HMAC y ventana de tiempo;
  - tamaños acotados;
  - el texto de una noticia nunca es una instrucción (C-2).

## Presupuesto de latencia (objetivo, etapas 2-3)

| Paso | Objetivo |
|---|---|
| Llegada → evaluación de ingesta | < 1 ms por noticia (medido: p99 < 5 ms con 1.000 seguidas) |
| Capa rápida de NLP | < 5 ms |
| Riesgo + validación de mercado | < 10 ms (libro en caché ≤ 250 ms) |
| Envío al venue | latencia de red del exchange (paper: lognormal, mediana 80 ms) |

Con RSS, el cuello de botella es la fuente: se sondea cada 30-60 s y los medios publican el RSS con
demora. Para eventos de segundos hace falta un feed de baja latencia, que se enchufa por el webhook.

## Fallas y qué pasa

| Falla | Comportamiento |
|---|---|
| Una fuente cae o responde 5xx | Espera exponencial; a las 5 fallas se abre su circuito (prueba cada 10 min); las demás siguen |
| Una fuente devuelve 429 | Respeta `Retry-After` |
| Feed hostil o roto | Rechazado por seguridad o como XML inválido; se registra y se sigue |
| Telegram cae, se cuelga o limita | Solo se atrasan las alertas (cola con prioridad, RISK primero) |
| Token de Telegram inválido | Pausa de 5 min entre intentos; el motor sigue |
| Libro viejo o cruzado | La orden se rechaza (`sin_datos`) |
| Disco lleno al escribir el diario | Se cuenta la pérdida y se registra el error; el motor sigue |
| Corte de luz | Escrituras atómicas; el diario continúa la cadena al reiniciar |
| Bug que inunda de órdenes | Tope por minuto del sobre |
| Pánico | Archivo `KILL` |
