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
  - armado `armar-real` (frase exacta; sin tope de capital desde el 3-nov: todo el saldo, 30 días);
  - venue real (IOC con tope, margen aislado, stop de emergencia en el exchange, conciliación);
  - operador real en paralelo al de práctica (cuenta en `TRADING_HOME/real/`, nunca se publica).
- Hecho por el dueño: fusionó el PR, borró los secretos de MEXC de GitHub, actualizó la PC.
- Corregido: ¼-Kelly ahora dimensiona el riesgo (antes, el nominal: posiciones 50 a 100 veces más chicas).

## Simulacro, IA antes de entrar e investigación nueva (3-nov, tarde)

- **La PC ya se actualiza sola** (rama `pc` viva desde el 3-nov 16:04 UTC; 199 tests OK en Windows).
- **Lo que mostró la PC:** 3 cortos de práctica, los 3 por titulares de "hackeo" leídos solo por las reglas
  (certeza 1,0, así que no se consultaba a la IA): "Mercado Bitcoin" (exchange de Brasil) se tomó como BTC, y
  "los IOU de Liquid vuelven a estar respaldados" (buena noticia) como mala. Corregido:
  - la IA lee **toda** noticia que las reglas abrirían (`LiveTrader.rules_would_enter`); sin su lectura no se
    entra; con dinero real o simulacro, sin IA nunca (C-4 más estricta);
  - la IA da relevancia, importancia e impacto (0-10) y el vector toma el menor con el a priori;
  - nombres que no son el activo ("Mercado Bitcoin", "Bitcoin Cash", "Ethereum Classic"...) no cuentan;
  - si las dos cuentas preguntan lo mismo a la vez, una sola consulta.
- **Simulacro** (`armar-real --simulacro`, `execution/live/dryrun.py`): el operador real completo contra el
  libro real de MEXC, sin mandar órdenes; estado en disco; se publica en `pc` (`simulacro/`).
- **Real más robusto:** si MEXC ya cerró la posición (stop de emergencia o a mano), el programa se
  sincroniza y avisa en vez de reintentar para siempre; nunca cierra más de lo que MEXC tiene; el supervisor
  no actualiza con posiciones abiertas en ninguna cuenta.
- **Investigación nueva** ("Arquitectura Bot Trading Cuantitativo"): casi todo ya estaba (triple barrera,
  meta-labeling, ¼-Kelly, DSR, raíz cuadrada, Almgren-Chriss, Kyle, decaimiento del alfa, freno diario que
  cierra todo). Agregado: guarda de libro despegado del precio de marca (> 1 %), funding en contra como costo
  de la operación, y las 5 variables leídas por la IA. Corregido de paso: la caché de funding nunca se
  renovaba (una entrada de hoy recibía el "próximo cobro" de ayer).
- **No se tomó:** MinHash/LSH (nuestro volumen es chico y ya hay deduplicación), FinBERT/CausalStock/Janus-Q
  (modelos pesados; la lectura la hace Claude), Hyperliquid/Binance (el dueño opera MEXC), arbitraje de
  funding delta-neutral (rinde centavos con 100 USDT y tiene riesgo de cola), market making P2P en Argentina
  (otra estrategia y otras cuentas) y "segmentar" movimientos contra los umbrales de ARCA (eso sería evadir
  controles; sí sirve llevar registro completo de las operaciones para Ganancias y Bienes Personales).
- **Sin tope de capital** (el dueño: "elimina los topes, siempre se va a trabajar e invertir con todo el dinero
  disponible"): fuera `capital.max_live_usdt` y la calibración de 20 USDT; el real opera con todo el saldo de
  la cuenta (`LiveTrader.sync_capital`: sin posiciones abiertas, la caja es el saldo real; depósitos y retiros
  corren los frenos); el venue controla el margen contra el saldo disponible de MEXC; F-3 se mide sin frenar.
  Los porcentajes de riesgo del sobre no cambiaron (1 % por operación, 40 %, 80 %, 30 %, 2 %, 4 %, 8 %, 1x).
- Ruido de Windows (WinError 10054 al cerrar conexiones) fuera de `errores.jsonl`.

## Todo lo que mueva la noticia (3-nov, noche)

- Pedido del dueño: "operar con distintas monedas, cripto, acciones o lo que sea necesario según la noticia".
- `marketdata/universe.py`: el catálogo público de MEXC futuros (activos, por API, con volumen de 24 h ≥ 2 M
  USDT), unos 120 activos: criptos, acciones, índices, metales y petróleo. Caché `datos/universo-mexc.json`,
  renovada cada 6 h. `research/assets.register` suma esos activos al reconocimiento (nombres de empresas y
  materias primas; tickers solo escritos como ticker) y `MexcFutures.symbol` los mapea (TSLA → TESLA_USDT).
- La IA nombra cualquier activo del universo (la lista va en el prompt) y se valida igual (C-1).
- Categorías nuevas: `resultados` (supera o no lo esperado, sube o baja la guía) y `fusion` (siempre la decide
  la IA: la comprada sube, la que compra suele bajar).
- Fuentes nuevas: `cnbc.top`, `cnbc.earnings`, `seekingalpha`, `investing`.
- Armado real con activos "todos" por defecto.
- **MEXC spot, todavía no (datos del 3-nov):** de 1.367 pares USDT, solo 22 mueven ≥ 2 M USDT por día y solo
  6 no tienen futuro en MEXC (ZIG, PUMP, XAUT, UPC y dos stablecoins). Futuros a 1x cobran menos (0 a 0,02 %
  contra 0,05 %), permiten cortos y no se liquidan. Cuando aparezca un caso (un listado nuevo solo en spot y
  líquido): `MexcSpot` como `BinanceSpot` (la API v3 de MEXC es compatible en libro, velas y ticker; los
  filtros vienen como precisiones: `quotePrecision`, `baseAssetPrecision`, `baseSizePrecision`,
  `quoteAmountPrecision`), primero en práctica; el real necesita enmendar A-3 (`/api/v3/order`).

## Más inteligente (3-nov, noche)

- **No corre detrás del precio** (`policy.news_move` / `chase_blocks`): si desde la publicación el precio ya
  recorrió el objetivo, no entra (la ganancia ya la tomaron otros); si fue en contra más que el stop, tampoco
  (el mercado no le cree a la noticia). El análisis de cada decisión guarda `movido_desde_la_noticia`.
- **Anuncios oficiales de listados** (`ingestion/announcements.py`): Binance (catálogo 48), OKX y Bitget, cada
  15 a 30 s, por la misma tubería que un RSS (`SourceSpec.format`). Bybit y Upbit bloquean por país.
- El universo baja el piso de liquidez a 1 M USDT por día (~170 activos): los listados suelen ser monedas
  chicas; el libro se revisa igual contra el tamaño de la orden.
- Tickers también como `FOO/USDT` ("OKX to list FOO/USDT").

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
errores). Al lado: `paper-vivo/<mercado>/` y `simulacro/<mercado>/` (estado, operaciones, decisiones
recientes), `errores.jsonl`, `tests.txt`, `supervisor.json` y `agente-salida.txt`. La cuenta de dinero real
nunca se publica (G-8).

## Pasos siguientes (en orden)
1. Que el dueño fusione el PR protegido (CI ignora `pc`, prueba en Windows, G-7/G-8).
2. Mirar la rama `pc` cada día: motivos de descarte, PnL, deslizamiento, tests en Windows.
3. Juntar evidencia: F-3 pide 60 operaciones cerradas en 21 días con DSR ≥ 0,95 (la da la PC).
4. Calibración con ≥ 30 etiquetas por categoría: κ, M por categoría, motivos de descarte. Fuentes más
   rápidas (webhook) si el RSS llega tarde.
5. Dinero real con todo el saldo desde el armado del dueño (A-2); seguir la evidencia (F-3) en el tablero.

## Reglas que no cambian
- Nunca tocar el repo ni las carpetas de nexus.
- Dinero real solo con el armado del dueño (A-2) y solo desde `execution/live/` (`test_a3`).
- No aflojar `config/limits.yaml`.
- No imprimir ni publicar secretos (el repo, los logs y la rama `pc` son públicos).
- Acciones fijadas por SHA.
- Rutas protegidas: solo con aprobación del dueño.
