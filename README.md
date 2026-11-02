# TRADING · agente autónomo guiado por noticias

Agente de trading que lee noticias críticas en tiempo real: Fed, SEC, presentaciones 8-K y medios cripto.
Filtra el ruido y lo viejo, modela el impacto en el mercado y opera **solo, dentro de un sobre de riesgo**
que fija el dueño. Avisa cada operación por Telegram. **Esta versión opera en paper**: los libros de
órdenes son reales y los llenados son simulados con latencia, deslizamiento y comisiones. El dinero real
llega en la etapa 4, con armado del dueño.

Es un proyecto aparte de nexus: carpeta, datos y claves propias. Ver [docs/OPERACION.md](docs/OPERACION.md).

## Estado

| Etapa | Qué | Estado |
|---|---|---|
| 1 | Ingesta (RSS/Atom, webhook, dedup semántica-léxica, ruido, staleness) · paper trading riguroso · Telegram fail-safe · diario encadenado · latido · reglas v2 | ✅ |
| 1½ | **Recolector de entrenamiento** en GitHub Actions: noticias + reacción del precio cada 15 min, en la rama `data` ([cómo activarlo](docs/OPERACION.md#entrenamiento-en-la-nube-github-actions-sin-la-pc)) | ✅ 99 tests (3.11 y 3.13) |
| 2 | Inteligencia: entidades, categoría, polaridad y convicción, con grounding y calibración | Siguiente |
| 3 | Decisión y riesgo: tamaño por ATR y convicción, brackets, circuit breakers, validación de mercado | — |
| 4 | Conector real (CEX) con conciliación y armado local | — |
| 5-6 | Tablero, comandos de Telegram, promoción a real por evidencia | — |

Plan y criterios de cada etapa: [docs/PLAN.md](docs/PLAN.md). Arquitectura: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).
Reglas: [docs/REGLAS.md](docs/REGLAS.md). Qué se aprovechó de la base anterior: [docs/LEGADO.md](docs/LEGADO.md).

## Uso rápido

```powershell
git clone https://github.com/bbarrionuevo/Trading.git $env:USERPROFILE\TRADING
cd $env:USERPROFILE\TRADING
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest                          # todo verde
.\.venv\Scripts\python -m tradingagent telegram-test      # con el .env en %LOCALAPPDATA%\TRADING
.\.venv\Scripts\python -m tradingagent sources --check    # lee cada fuente una vez
.\.venv\Scripts\python -m tradingagent run                # agente completo en paper
.\.venv\Scripts\python -m tradingagent paper-sim examples\escenario_noticia.jsonl
.\.venv\Scripts\python -m tradingagent collect           # una pasada del recolector de entrenamiento
```

## Estructura

```
src/tradingagent/
  core/         reloj, bus con colas acotadas, lag del bucle, eventos, redacción de secretos
  ingestion/    parser seguro, fuentes por sondeo, webhook, replay, normalización, dedup, ruido, motor
  marketdata/   libro L2 (Decimal) y proveedores
  execution/    órdenes, instrumentos, venue paper, cartera, servicio de ejecución, harness
  alerts/       Telegram: modelo, cliente de la Bot API, despachador en hilo propio
  telemetry/    diario encadenado, latido, logs
  research/     recolector de entrenamiento: activos mencionados, reacción del precio, corridas con estado
  limits.py     sobre de riesgo (protegido)      safety.py   núcleo de seguridad (protegido)
config/limits.yaml   el sobre (protegido)        config/agent.example.yaml   ejemplo de configuración
tools/               chequeos del CI (protegidos)
tests/               99 tests, entre ellos los de las reglas (test_rules.py, protegido)
```

## Desarrollo

`pip install -e ".[dev]"`, `ruff check src tests tools`, `python -m pytest`. Se desarrolla en la rama
`claude/nice-sagan-inp0u8` y se abre PR a `main`. Si el PR toca rutas protegidas, el control
`protected-paths` queda en rojo y solo lo fusiona el dueño (REGLAS G-2).
