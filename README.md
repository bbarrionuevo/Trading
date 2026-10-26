# nexus

Sistema de investigación y trading automatizado con capital pequeño (100 €). **Estado actual: Fase 5 — paper
trading.** No existe código que envíe órdenes reales; activar dinero real exige construirlo y la autorización
explícita del dueño ([docs/REGLAS.md](docs/REGLAS.md)).

## Qué hace hoy (verificado)
| Componente | Dónde corre | Qué hace |
|---|---|---|
| Agente de market making (`nexus.agent.maker_agent`) | PC Windows 11 | Paper trading en MEXC (BONK, AI) con modelo de cola sobre datos reales; riesgo con veto y persistente |
| Actualizador seguro (`local/windows/update.py`) | PC, cada 10 min | Instala solo `main` con CI en verde, sin posiciones abiertas, con tests locales y vuelta atrás |
| Vigilante y informe diario (`nexus.monitor`) | GitHub Actions | Alertas cada hora e informe diario como issues → mail |
| Descubrimiento (`discover.yml`) | GitHub Actions | Escanea arbitraje triangular, entre exchanges, paridad de stablecoins, spreads y ARS (datos públicos) |
| Investigación (`research-maker.yml`, `trading-backtest.yml`, `live-paper.yml`) | GitHub Actions | Backtests walk-forward y paper en vivo; resultados en la rama `data` |

## Evidencia hasta ahora
| Estrategia | Resultado |
|---|---|
| Arbitraje triangular, entre exchanges, EUR↔ARS, P2P/Fiwind, paridad EURC | Negativo neto, descartadas |
| Trading direccional (RSI/SMA/breakout, 20 monedas, walk-forward) | Sin ventaja robusta |
| Market making con maker 0,1% o en pares FDUSD | Negativo |
| **Market making con maker 0% en MEXC (BONK, AI)** | Señal positiva débil en ~6,5 h de paper → **en validación** en el PC |

## Documentación
- [docs/REGLAS.md](docs/REGLAS.md): reglas inquebrantables, estado de cada una y criterios de fase.
- [docs/LIMITES.md](docs/LIMITES.md): límites de riesgo para 100 € y su derivación.
- [docs/OPERACION_WINDOWS.md](docs/OPERACION_WINDOWS.md): instalación, parada de emergencia, actualización y rearme.
- [docs/SESION_DIARIA.md](docs/SESION_DIARIA.md): qué hace (y qué no) la sesión diaria automática de Claude.
- [docs/SESION_PC.md](docs/SESION_PC.md): instrucciones para la sesión de Claude en el PC (canal: rama `pc-sesion`).

## Desarrollo
```
pip install -r local/requirements.txt
python backtest/run.py           # control de seguridad (límites y modo real)
cd local && python -m pytest -q  # tests, incluidas las reglas
```
Ramas: desarrollo en `claude/nice-sagan-inp0u8` → PR a `main` (lo que instala el PC). `data` y `pc-status`
son ramas de datos escritas por bots.
