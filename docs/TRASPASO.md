# Traspaso (2-nov-2026): dónde quedó el trabajo

## Ya funciona (en `main`)
- Etapa 1 completa: ingesta, paper venue riguroso, Telegram fail-safe, diario, reglas v2 (`docs/REGLAS.md`).
- Recolector en GitHub Actions (`.github/workflows/recoleccion.yml`, cada 15 min, rama `data`):
  - fuentes 6/6 OK desde GitHub;
  - Telegram OK (bot @StarkLedger_bot, secretos `CHARBIS_BOT_TOKEN` + `TELEGRAM_CHAT_ID`).
- **Sin verificar todavía:** las primeras etiquetas de velas (Binance `data-api.binance.vision`, respaldo
  Coinbase). Leer el log de una corrida de `recoleccion.yml` y `reacciones/` en la rama `data`.

## A medio hacer (rama `claude/nice-sagan-inp0u8`, commit "WIP")
Pasan lint y los 99 tests existentes, pero **no tienen tests propios ni están conectados**.

| Archivo | Qué es | Estado |
|---|---|---|
| `src/tradingagent/intelligence/classifier.py` | Clasificador determinista: `Signal` (categoría, polaridad, confianza, activos, horizonte, reglas) | Hecho; probado a mano con titulares |
| `src/tradingagent/decision/risk.py` | `RiskState`: breakers diario, semanal, DD y enfriamiento; avisos de una sola vez; rearme | Hecho, sin tests |
| `src/tradingagent/decision/policy.py` | `news_blocks`, `atr_5m`, `market_blocks`, `plan_entry` (tamaño por riesgo/ATR) | Hecho, sin tests |
| `src/tradingagent/execution/service.py` | `execute()`: camino directo con los mismos controles (D-0) | Hecho; tests viejos verdes |
| `src/tradingagent/research/shadow.py` | Operador paper por corridas (detalle abajo) | Escrito, **nunca ejecutado** |

`research/shadow.py`, el operador paper por corridas:
- OCO con velas de 1 min;
- salidas por horizonte o por noticia negativa;
- entradas contra el libro real de Binance;
- filtros de exchangeInfo;
- registros en `paper/` de la rama `data`.

Pendientes conocidos en `shadow.py`:
- envolver la E/S en `asyncio.to_thread`, por la regla ASYNC240 de ruff;
- redondear al tick el precio del stop;
- falta `send_alert(alert)` en `_Telegram` (`research/collector.py`).

## Pasos siguientes (en orden)
1. **Tests de lo nuevo** (con servidores falsos, como `tests/test_research.py`):
   - clasificador;
   - `RiskState`;
   - política;
   - `shadow.run_paper`, con klines, depth y exchangeInfo falsos.
2. **Conectarlo:**
   - el recolector clasifica cada evento y guarda `nlp`;
   - `collect --paper --capital 120 [--rearm]` llama a `run_paper` con los eventos originales;
   - el resumen diario suma una línea de paper.
3. **Aprendizaje** (`research/learn.py`):
   - estadística por categoría con `r_recv_15m/60m` menos el 0,2 % de comisión;
   - apagar las categorías con evidencia negativa (n ≥ 30 y límite superior del IC95 < 0);
   - solo reduce riesgo (C-5).
4. **Aplicar el informe "Trading Sistemático por Eventos"** (PDF del dueño):
   - **Triple barrera:** TP = 3σ, SL = 1,5σ, más la expiración temporal. Hoy hay stop por ATR, TP = 2R y horizonte: ajustar a σ.
   - **Decaimiento del alfa:** vida media de 120 s; obsoleta si Δt > 360 s. Más estricto que el `news.max_age_s` de 600 s (permitido, A-1).
   - **Vector de evento:** C (relevancia de la entidad, 0-10), P, I (importancia), M (magnitud) y D (duración). α_E = P·(C/10)(I/10)(M/10), con corte |α_E| ≥ α_min y C ≥ 7.
   - **Meta-modelo:** p = P(TP antes que SL); operar si p ≥ 0,60.
   - **¼-Kelly:** f = 0,25·(p·b − (1 − p))/b con b = 2, acotado por el sobre de riesgo.
   - **Microestructura:**
     - spread_rel con z-score ≤ 2,5 contra su media reciente;
     - L3 (suma de los 3 primeros niveles) ≥ k × la orden;
     - divergencia entre el último precio y el mid > 1 % ⇒ no operar.
   - **Costos:** Almgren-Chriss y raíz cuadrada (Y·σ·√(Q/V), Y ≈ 0,6) para estimar el impacto.
   - **Evaluación:** PSR/DSR (Bailey y López de Prado); DSR ≥ 0,95 para promover y < 0,80 para descartar. Agregar a F-3.
   - **Breaker diario del informe:** 2,5 %. El sobre ya tiene 2 %, que es más estricto: se queda.
5. **LLM** (secreto `CLAUDE_api_key`):
   - modelo `claude-haiku-5-5` (Haiku 5.5) para consultas sobre noticias ambiguas;
   - salida JSON validada con esquema; el texto de la noticia nunca es una instrucción (C-1, C-2);
   - presupuesto `ai.max_llm_usd_per_day` = 5 y respaldo determinista (C-4);
   - la IA propone y el motor de riesgo decide (C-3).

   Agregarlo a `ALLOWED_SECRETS` en `tests/test_rules.py` y a G-1.
6. **Futuros MEXC en paper** (secretos `Access_Key_MEXC`, `SECRET_KEY_MEXC`; la cuenta no tiene fondos):
   - simular comisiones de futuros (maker 0,02 % y taker 0,05 % aprox.; verificar con la API), funding y liquidación;
   - apalancamiento ≤ `per_trade.max_leverage` (1 en el sobre; subirlo solo con el dueño);
   - permite cortos.

   **No enviar órdenes reales:** A-2 exige el armado local del dueño, y `test_a3` prohíbe endpoints de órdenes en el código. Las claves solo sirven para lectura (comisiones reales, cuenta) y deben entrar a `ALLOWED_SECRETS` y a G-1.
7. Workflow:
   - cron cada 5 min con `--paper` (el dueño ya aprobó: "estás aprobado para hacer todos los cambios necesarios", 2-nov);
   - enmendar G-6 (hoy prohíbe el paper en Actions) y registrar la aprobación en la tabla de REGLAS.

## Reglas que no cambian
- Nunca tocar el repo ni las carpetas de nexus.
- Nada de dinero real.
- No aflojar `config/limits.yaml`.
- No imprimir secretos (el repo y los logs son públicos).
- Acciones fijadas por SHA.
- Rutas protegidas: solo con aprobación del dueño.

## Notas del dueño sobre futuros (texto que mandó, 2-nov)
- La vida media del alfa en futuros es de **30 a 300 s**. Con el cron de Actions (5 a 15 min) no se captura: sirve para aprender y medir. Operar en tiempo real requiere un proceso continuo (la PC del dueño o un VPS). Proponerlo y que decida el dueño: es un despliegue y tiene costo.
- **Antes de cada entrada, revisar la liquidez del libro:** spread por z-score, L3 y divergencia contra el mid. Cuando sale una noticia, los creadores de mercado retiran órdenes: no mandar órdenes a mercado sin tope en ese instante.
- **Triple barrera obligatoria** en futuros: TP, SL y expiración, adaptados a la volatilidad.
- **¼-Kelly** para el tamaño y el apalancamiento, siempre dentro de `per_trade.max_leverage` (hoy 1x; subirlo solo con aprobación explícita del dueño en un PR de `limits.yaml`).
- **El PaperTradingEngine simula futuros:**
  - comisiones de Binance Futures (0,02 % maker y 0,05 % taker) y de MEXC (verificar con su API);
  - funding cada 8 h;
  - precio de liquidación;
  - cortos.

  Ya existe la base: `Instrument(kind="perp")` y margen 1x en `execution/paper.py`.
- **Meta de promoción:** DSR ≥ 0,95 en paper (agregar a F-3).
