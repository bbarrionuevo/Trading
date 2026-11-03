# Traspaso (3-nov-2026): dónde quedó el trabajo

## Hecho en esta sesión (rama `claude/nice-sagan-inp0u8`)

| Paso | Qué | Dónde |
|---|---|---|
| 1 | Tests del clasificador, frenos, política y operador paper (servidores falsos de Binance, MEXC, Telegram, RSS y Anthropic) | `tests/` |
| 2 | El recolector clasifica cada noticia (`nlp`); `collect --paper` opera en paper; línea de paper en el resumen diario | `research/collector.py` |
| 3 | Aprendizaje que solo reduce riesgo: apaga "categoría:lado" con n ≥ 30 e IC95 < 0; ajusta κ ≤ 1 del meta-modelo | `research/learn.py` |
| 4 | Informe "Trading Sistemático por Eventos": vector C/P/I/M/D, alfa con t½ = 120 s (obsoleta a 360 s), meta-modelo p ≥ 0,60, triple barrera 1,5σ/3σ, ¼-Kelly con b neto, paridad de volatilidad, exposición neta ≤ 30 %, spread z ≤ 2,5, L3, divergencia ≤ 1 %, Kyle, raíz cuadrada y Almgren-Chriss, PSR/DSR, freno diario y halt que cierran todo | `intelligence/`, `decision/`, `execution/costs.py`, `marketdata/liquidity.py`, `research/evaluation.py` |
| 5 | Claude Haiku 5.5 para noticias dudosas (SDK `anthropic` 2.4.0, esquema, grounding, presupuesto, sin reintentos) | `intelligence/llm.py` |
| 6 | Paper de futuros de MEXC: cortos, comisiones D-5, funding, liquidación; cliente privado de solo lectura con lista cerrada | `marketdata/mexc.py`, `research/futures.py` |
| PC | Operador en vivo para la PC 24/7 (mismo núcleo, barreras cada segundo, estado persistente) + tarea `trading-agente` | `live.py`, `deploy/windows/` |

**Verificado en GitHub Actions (logs reales del 3-nov):**

- El recolector lee las 6 fuentes y etiqueta con velas de Binance (`data-api.binance.vision`) desde GitHub
  (corrida programada a las 00:53 UTC).
- Corrida desde la rama del PR de reglas, con los secretos reales (runs 41561320789 y 41561580548):
  - **MEXC responde desde GitHub:** especificación real de BTC/ETH/SOL (contrato de 0,0001, 0,01 y 0,1).
  - **Las claves de solo lectura de MEXC funcionan:** firma aceptada; comisión de la cuenta 0,01 % maker y
    0,04 % taker. Por D-5 se usa la configurada, 0,02 % y 0,05 %, que es la mayor.
  - **Claude Haiku 5.5 responde con `CLAUDE_API_KEY`:** el chequeo del día dio `ok` y costó 0,00019 USD.
  - Paper en las dos cuentas. La primera noticia se descartó con los motivos correctos (alfa obsoleta a los
    525 s, C = 3 y p = 0,34).
- El cron de 15 minutos tardó horas en dar su primera corrida (scheduler de GitHub): no es un error del repo.

## Pendiente de la aprobación del dueño (bbarrionuevo/Trading#7, rutas protegidas)

Paso 7 y reglas v3:

- workflow cada 5 min con `--paper` y los secretos `CLAUDE_API_KEY`, `ACCESS_KEY_MEXC` y `SECRET_KEY_MEXC`;
- G-1 y G-6 enmendadas, G-7 nueva, F-3 con DSR y tabla de aprobaciones;
- `ALLOWED_SECRETS` y `test_a3` endurecido (endpoints de órdenes de MEXC prohibidos);
- `max_net_exposure_pct` en el sobre.

Hasta que se fusione, el cron de `main` sigue cada 15 min, sin paper y sin los secretos nuevos.

## Pasos siguientes (en orden)
1. Que el dueño revise y fusione el PR de reglas v3 (bbarrionuevo/Trading#7). Desde ahí el paper corre
   cada 5 min en Actions.
2. Instalar el agente en la PC (`deploy/windows/instalar-agente.ps1`) con el `.env` (Telegram, Claude,
   claves de solo lectura de MEXC) y mirar el primer día de `paper-vivo`.
3. Juntar evidencia: F-3 pide 60 operaciones cerradas en 21 días con DSR ≥ 0,95. Con el cron casi no
   habrá operaciones (el alfa decae antes): la evidencia sale de la PC.
4. Calibración: cuando haya ≥ 30 etiquetas de triple barrera por categoría, revisar κ, M por categoría y
   los motivos de descarte (`decisiones/`). Fuentes más rápidas (webhook) si el RSS llega tarde.
5. Etapa 4 (conector real con armado local) recién con F-3 cumplido y el armado del dueño (A-2).

## Reglas que no cambian
- Nunca tocar el repo ni las carpetas de nexus.
- Nada de dinero real (solo paper; no hay código de órdenes reales: `test_a3`).
- No aflojar `config/limits.yaml`.
- No imprimir secretos (el repo y los logs son públicos).
- Acciones fijadas por SHA.
- Rutas protegidas: solo con aprobación del dueño.
