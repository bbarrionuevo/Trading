# Traspaso (3-nov-2026): dónde quedó el trabajo

## Hecho (rama `claude/nice-sagan-inp0u8`, todo en `main`)

| Paso | Qué | Dónde |
|---|---|---|
| 1 | Tests del clasificador, frenos, política y operador paper (servidores falsos de Binance, MEXC, Telegram, RSS y Anthropic) | `tests/` |
| 2 | El recolector clasifica cada noticia (`nlp`); `collect --paper` opera en paper; línea de paper en el resumen diario | `research/collector.py` |
| 3 | Aprendizaje que solo reduce riesgo: apaga "categoría:lado" con n ≥ 30 e IC95 < 0; ajusta κ ≤ 1 del meta-modelo | `research/learn.py` |
| 4 | Informe "Trading Sistemático por Eventos": vector C/P/I/M/D, alfa con t½ = 120 s (obsoleta a 360 s), meta-modelo p ≥ 0,60, triple barrera 1,5σ/3σ, ¼-Kelly con b neto, paridad de volatilidad, exposición neta ≤ 30 %, spread z ≤ 2,5, L3, divergencia ≤ 1 %, Kyle, raíz cuadrada y Almgren-Chriss, PSR/DSR, freno diario y halt que cierran todo | `intelligence/`, `decision/`, `execution/costs.py`, `marketdata/liquidity.py`, `research/evaluation.py` |
| 5 | Claude Haiku 5.5 para noticias dudosas (SDK `anthropic` 2.4.0, esquema, grounding, presupuesto diario que sobrevive a reinicios, sin reintentos) | `intelligence/llm.py` |
| 6 | Paper de futuros de MEXC: cortos, comisiones D-5, funding, liquidación; cliente privado de solo lectura con lista cerrada | `marketdata/mexc.py`, `research/futures.py` |
| 7 | Paper cada 5 min en GitHub Actions con los secretos de Claude y MEXC; reglas v3 (bbarrionuevo/Trading#7) | `.github/workflows/recoleccion.yml`, `docs/REGLAS.md` |
| PC | Operador en vivo 24/7 (mismo núcleo, barreras cada segundo, estado persistente) | `live.py` |
| PC | Instalador de un comando (bbarrionuevo/Trading#8) | `deploy/windows/instalar.ps1` |
| PC | **Supervisor:** relanza al agente, se actualiza solo con CI verde y sin posiciones abiertas (vuelve atrás si no arranca), corre los tests en la PC, un solo agente por carpeta (candado + job de Windows) y publica el estado en la rama `pc` | `supervisor.py`, `core/instance.py` |
| PC | **Tablero** local de solo lectura en http://127.0.0.1:8765 | `dashboard.py` |

**Verificado:** logs reales de GitHub Actions (MEXC público y claves de solo lectura, Claude Haiku, velas de
Binance) y la PC del dueño (instalador completo, Telegram, tarea `trading-agente` corriendo).

## Pendiente de la aprobación del dueño (rutas protegidas, bbarrionuevo/Trading#10)

- `ci.yml`: ignorar la rama `pc` (si no, cada publicación dispararía el CI) y el job `windows` (tests,
  sintaxis de los scripts en PowerShell 5.1 y registro real de la tarea). Hasta que se fusione, el
  supervisor no publica.
- `paths.py`: `write_json_atomic` reintenta en Windows si otro proceso tiene abierto el archivo (lo
  encontró el primer CI en Windows real: 186 de 187; con el arreglo, todo verde).
- REGLAS: G-7 (el supervisor y la actualización automática) y G-8 nueva (la rama `pc`: qué se publica).

## Cómo ver la PC (para Claude)

Rama `pc` del repo, un solo commit que se reemplaza cada 15 min: empezar por `tablero.json` (estado,
cuenta, posiciones, últimas operaciones y decisiones, motivos de descarte, salud, IA, supervisor, tests y
errores). Al lado: `paper-vivo/<mercado>/` (estado, operaciones, decisiones recientes), `errores.jsonl`,
`tests.txt`, `supervisor.json` y `agente-salida.txt`.

## Pasos siguientes (en orden)
1. Que el dueño fusione el PR protegido (CI ignora `pc`, prueba en Windows, G-7/G-8).
2. Mirar la rama `pc` cada día: motivos de descarte, PnL, deslizamiento, tests en Windows.
3. Juntar evidencia: F-3 pide 60 operaciones cerradas en 21 días con DSR ≥ 0,95 (la da la PC).
4. Calibración con ≥ 30 etiquetas por categoría: κ, M por categoría, motivos de descarte. Fuentes más
   rápidas (webhook) si el RSS llega tarde.
5. Etapa 4 (conector real con armado local) recién con F-3 cumplido y el armado del dueño (A-2). La
   calibración con dinero real (F-4) es de ≤ 20 USDT.

## Reglas que no cambian
- Nunca tocar el repo ni las carpetas de nexus.
- Nada de dinero real hasta F-3 y el armado del dueño (no hay código de órdenes reales: `test_a3`).
- No aflojar `config/limits.yaml`.
- No imprimir ni publicar secretos (el repo, los logs y la rama `pc` son públicos).
- Acciones fijadas por SHA.
- Rutas protegidas: solo con aprobación del dueño.
