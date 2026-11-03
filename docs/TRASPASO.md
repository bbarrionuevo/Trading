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
| PC | **Tablero** local de solo lectura en http://127.0.0.1:18765 o el siguiente libre (el 8765 lo usa otro programa del dueño); acceso directo a `tablero.html` | `dashboard.py` |

**Verificado:** logs reales de GitHub Actions (MEXC público y claves de solo lectura, Claude Haiku, velas de
Binance) y la PC del dueño (instalador completo, Telegram, tarea `trading-agente` corriendo).

## Dinero real (pedido del dueño, 3-nov): `docs/DINERO-REAL.md`

- R1 construido en `execution/live/` (ruta protegida):
  - cliente de trading con lista cerrada y sin retiros;
  - prueba sin riesgo `mexc-real-check`;
  - armado `armar-real` (frase exacta, F-4: 20 USDT y 7 días);
  - venue real (IOC con tope, margen aislado, stop de emergencia en el exchange, conciliación);
  - operador real en paralelo al de práctica (cuenta en `TRADING_HOME/real/`, nunca se publica).
- Pendiente del dueño:
  - fusionar el PR protegido;
  - borrar los secretos de MEXC de GitHub (la clave puede operar);
  - correr `mexc-real-check`;
  - **decidir el sobre** para la cuenta chica: con el actual, 20 USDT no alcanzan para el contrato mínimo
    de BTC. Claude no lo puede aflojar solo.
- Corregido: ¼-Kelly ahora dimensiona el riesgo (antes, el nominal: posiciones 50 a 100 veces más chicas).

## Próximo: aprender de la historia y reaccionar más rápido (pedido del dueño, 3-nov)

> "Quizás sea bueno también buscar en historial para que pueda entender más rápido cómo repercute las noticias
> en el mercado. La idea es que pueda reaccionar lo más rápido posible a las noticias."

1. **Estudio histórico (`research/history.py`).**
   - Eventos con hora exacta:
     - anuncios de listado y deslistado de Binance (API pública de anuncios) y de Upbit (API pública de
       avisos), con años de historia;
     - decisiones de la Fed (14:00 hora de Nueva York) y datos de inflación (CPI, 08:30), con calendario fijo;
     - lo que ya junta la rama `data`.
   - Precio: velas de 1 minuto de Binance (`data-api.binance.vision`) y de MEXC.
   - Para cada categoría: reacción a 1, 5, 15 y 60 min, y resultado de la triple barrera con los costos
     reales.
   - Sirve para calibrar el meta-modelo y la tabla de magnitudes (solo reduce riesgo, C-5) y alimenta la
     revisión diaria.
   - Correrlo en la PC (internet completo); el resultado va a la rama `pc`.
2. **Fuentes más rápidas.** Sondear cada pocos segundos los avisos de Binance y Upbit: un listado nuevo
   mueve el precio en segundos y MEXC suele listar el futuro enseguida. Hoy el RSS se lee cada 30 a 60 s.

## Mensajes y uso de la IA (pedido del dueño, 3-nov)

- Telegram en castellano simple: sin siglas, montos a la argentina, qué hizo, cuánto puede perder o ganar
  y por qué (`alerts/model.py`). La práctica de GitHub no avisa operaciones sueltas; la PC manda un resumen
  diario a las 21:00 (hora argentina).
- Claude solo cuando cambia una decisión: hecho dudoso o concreto (no preguntas ni opiniones), noticia
  fresca, sin frenos, con lugar para operar o con posición abierta de ese activo; caché por titular; tope
  sin tope de uso (el dueño lo aclaró: siempre que haga falta y solo entonces); queda el techo del sobre
  (5 USD/día) contra errores; en GitHub apagado salvo `collect --ia`. El tablero cuenta las evitadas.

## Rutas protegidas

- Fusionado por el dueño (bbarrionuevo/Trading#10): el CI ignora la rama `pc` y prueba en Windows real
  (tests, sintaxis de los scripts en PowerShell 5.1 y registro real de la tarea); `paths.py` reintenta el
  guardado si Windows da "acceso denegado"; REGLAS v3.1 (G-7 y G-8).
- **Para el próximo PR protegido:** G-8 nombra el tablero en `http://127.0.0.1:8765`, pero ese puerto lo usa
  otro programa del dueño. Ahora es el 18765 o el siguiente libre, sin compartir nunca un puerto.

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
