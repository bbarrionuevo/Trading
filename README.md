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
| 1½ | **Recolector de entrenamiento** en GitHub Actions: noticias + reacción del precio, en la rama `data` ([cómo activarlo](docs/OPERACION.md#entrenamiento-en-la-nube-github-actions-sin-la-pc)) | ✅ |
| 2 | Inteligencia: clasificador determinista + Claude Haiku 5.5 para lo dudoso, vector de evento y alfa con decaimiento, meta-modelo | En marcha: falta calibrar con datos |
| 3 | Decisión y riesgo: triple barrera, ¼-Kelly, microestructura, costos, frenos que cierran todo, DSR; paper por corridas y **en vivo en la PC 24/7** (futuros de MEXC) | En marcha: falta la evidencia de F-3 |
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
.\.venv\Scripts\python -m tradingagent run                # agente completo en paper (opera en vivo)
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
  intelligence/ clasificador, Claude Haiku (dudosas), vector de evento y alfa, meta-modelo
  decision/     política (noticia → evento → plan → libro), triple barrera, frenos de riesgo
  research/     recolector, etiquetas, paper por corridas (Binance spot, MEXC futuros), aprendizaje, DSR
  live.py       operador paper en vivo (PC 24/7)
  supervisor.py la PC sin intervención: reinicio, actualización con CI verde, tests y estado en la rama pc
  dashboard.py  tablero local de solo lectura (http://127.0.0.1:8765)
  limits.py     sobre de riesgo (protegido)      safety.py   núcleo de seguridad (protegido)
config/limits.yaml   el sobre (protegido)        config/agent.example.yaml   ejemplo de configuración
tools/               chequeos del CI (protegidos)
deploy/windows/      instalador de un comando y tarea programada trading-agente (supervisor 24/7)
tests/               181 tests, entre ellos los de las reglas (test_rules.py, protegido)
```

## Desarrollo

`pip install -e ".[dev]"`, `ruff check src tests tools`, `python -m pytest`. Se desarrolla en la rama
`claude/nice-sagan-inp0u8` y se abre PR a `main`. Si el PR toca rutas protegidas, el control
`protected-paths` queda en rojo y solo lo fusiona el dueño (REGLAS G-2).
