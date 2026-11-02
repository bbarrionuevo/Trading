# Qué se tomó de la base anterior (nexus, oct-2026)

Este repo nació como copia del bot de market making de nexus: unas 13.700 líneas, R-01 a R-22,
11 workflows programados. El 2-nov-2026 ese código se retiró del árbol: queda completo en el historial
(commit `aeca87b`, `git show aeca87b:local/nexus/...`). Lo que valía se trajo como ideas o como diseño probado:

| De la v1 | En TRADING |
|---|---|
| Límites para 100 € (2%/día, 4%/semana, 8% drawdown con rearme humano, 3 stops + 60 min, 30 órdenes/min, posición 40%, exposición 80%) | Mismos valores, en % del equity, en `config/limits.yaml` (línea base en `limits.py`) |
| Riesgo con veto, independiente de las estrategias (R-03) | Núcleo de seguridad `safety.py` y motor de riesgo (etapa 3) |
| KILL de 3 niveles (R-16) | E-1 (nivel 1 ya activo) |
| Idempotencia con ID propio guardado antes de enviar (R-06) | D-1 (paper ya; real en la etapa 4) |
| Conciliación de estado desconocido y bloqueo por rechazos (R-05, R-08) | D-4 y D-6, en la etapa 4 |
| Salida forzada con límite agresivo con tope, re-preciado (R-08) | Stops del venue paper: tope `stop_slippage_bps`, re-precio en cada libro |
| Modelo de llenado 5: el límite se llena solo si el mercado lo atraviesa | `PaperConfig.limit_fill_rule = "through"` |
| Diario de órdenes con hash encadenado (R-10) | `telemetry/journal.py` para todo: noticias, decisiones, órdenes |
| Credenciales fuera del repo, redacción de secretos (R-11) | G-1 y `core/redact.py` |
| Rutas protegidas y gate del CI (R-04, R-14, R-18) | G-2, `tools/protected_paths.py`, `tools/check_limits.py` |
| Criterios de evidencia antes de dinero real (R-15) y excepción de calibración | F-3 y F-4, adaptados a operaciones guiadas por noticias |
| Disciplina contra el sobreajuste: grupo de control, evidencia por versión (docs/DIRECCION.md) | C-5 |

## Evidencia de la v1 (para no repetir errores)

- **Sin ventaja:** arbitraje triangular, arbitraje entre exchanges, paridad de stablecoins y trading
  direccional por indicadores (RSI, medias, rupturas) dieron neto negativo o sin ventaja robusta en
  walk-forward.
- **Market making con 0% maker en MEXC:** positivo con el modelo de llenado optimista, negativo con el
  conservador. **Lección:** el modelo de llenado decide si una estrategia "gana". Por eso el paper de
  TRADING es conservador de entrada.

## Qué se retiró y por qué

- **Workflows programados:** 11, entre ellos `discover` cada 20 minutos y `watchdog` cada hora con
  alertas por mail. Fallaban en este repo (buscaban la rama `data` y el latido del PC) y no sirven al
  agente nuevo.
- **Código de exchanges con órdenes reales de la v1:** lo reemplaza el conector de la etapa 4, detrás de
  la interfaz del venue y con armado local.
- **Instalador, actualizador y tareas de Windows de la v1:** usaban las carpetas y tareas de nexus. TRADING
  tendrá las suyas (etapa 5), con nombres propios.
