# Mapa de oportunidades (actualizado 24-oct-2026, noche)

Objetivo: hacer crecer el capital componiendo ganancias, con estrategias que **escalen** cuando el
capital crezca. Todo se mide primero con datos reales y en paper; nada pasa a dinero real sin cumplir
los criterios aprobados (docs/REGLAS.md, R-15) y sin autorización del dueño.

El informe vivo está en la rama `data`: `data/radar.md` (cada 2 h), `data/feasibility.md` (cada 6 h),
`data/live_paper_hedge.md` y el latido del PC en `pc-status`.

## Lo que ya se descartó con datos (y por qué)
| estrategia | resultado |
|---|---|
| Triangular (Binance, Kraken), cruce entre exchanges en EUR, peg de EURC | neto negativo tras comisiones |
| Comprar en una app argentina y vender en otra como tomador | −1% a −3% |
| Direccional (RSI, cruces de medias, rupturas) | sin ventaja en backtest |
| Market making simple en MEXC (0% maker) | con el modelo de llenado corregido, **negativo**: los stops se comen las ganancias; en mejora (pausa de pares tóxicos, sondeo en paralelo) |
| Cobertura MEXC→Binance con 100 € | positiva pero mínima: el inventario se agota de un lado |

## Candidatas vivas (ordenadas por potencial)
| # | estrategia | qué se gana | escala | automatizable | estado |
|---|---|---|---|---|---|
| 1 | **Diferencia de funding entre exchanges** (corto donde paga más, largo donde paga menos; delta neutral) | hoy IRYS MEXC/OKX: +128% APR **realmente cobrado** los últimos 7 días, todos los días positivos | alta: decenas de miles de USD por moneda líquida | sí, por API (OKX, KuCoin, Bitget, Gate, Hyperliquid) | paper con cobros reales desde 24-oct |
| 2 | **Rulo local**: pesos → dólar oficial (banco) → USDT pagando con dólares (Binance P2P en USD, AstroPay, Xapo) → pesos | 24-oct: +4,8% por vuelta vía Binance P2P (prima 1,8%), hasta +6,7% vía Xapo (prima 0,02%). Fiwind cobra 6,5% de prima: por esa vía no rinde | limitada por el cupo mensual de dólares de tu banco y tus límites AR | parcial: el radar avisa y las patas cripto van por API; comprar dólares y el P2P son manuales | **prueba chica manual pendiente** (ver abajo) |
| 2b | **Vuelta internacional** USD afuera → USDT → pesos → dólar oficial → giro al exterior | +6,6% bruto; con giro de 40 USD + 0,3%: +2,2% con 1.000 USD, +5,4% con 5.000 USD | alta, pero el costo fijo del giro exige montos grandes | parcial | a verificar costo del giro |
| 3 | **Carry de funding** (contado + corto perpetuo en el mismo exchange) | JCT: 36–120% APR según el día | media | parcial (MEXC no deja abrir futuros por API) | paper desde 24-oct |
| 4 | **Merchant P2P** (publicar avisos de compra y venta) | El Dorado: 1,8% bruto entre puntas; aviso de venta cubierto en Binance: ~+0,4% por llenado | media, limitada por cuentas bancarias y KYC | poco (los avisos y los pagos son manuales) | medición en el radar |
| 5 | **Préstamo de stablecoins** (Bitfinex margin funding) | ~9–11% APR, sin riesgo de precio | muy alta | sí (Bitfinex permite automatizar préstamos) | medición en el radar |
| 6 | **Launchpools y promociones** de exchanges | APR alto con cupo por usuario: ideal para capital chico | baja (cupos) | aviso automático; participación manual | radar de eventos (MEXC, Bitget) |

## Qué necesito que verifiques vos (no se puede desde acá)
1. **Rulo local** (el dueño confirmó el 24-oct que puede comprar dólares con pesos y tiene cuenta en
   dólares en un banco argentino):
   - ¿Hay **tope mensual** para comprar dólares en tu banco? ¿Alguna **restricción cruzada** por vender
     cripto o hacer MEP?
   - ¿Tu banco deja **transferir dólares a terceros** (necesario para pagar un USDT en Binance P2P en
     USD)? ¿AstroPay o Xapo aceptan cargar dólares desde tu cuenta argentina, a qué costo y en cuánto tiempo?
   - **Prueba chica manual** (p. ej. 100 USD): comprar dólares → comprar USDT pagando en dólares →
     vender el USDT por pesos (Binance USDT/ARS) → comparar los pesos finales con los iniciales.
     Si confirma ~4-5%, se automatiza lo automatizable: aviso de cuándo conviene, montos y las patas cripto.
   - Riesgos del P2P: usar contrapartes verificadas (riesgo de fondos de terceros y bloqueos bancarios).
2. **Vuelta internacional**: costo real del giro al exterior de tu banco y de la acreditación en Kraken.
2. **Cuentas**: para la diferencia de funding hacen falta dos exchanges con futuros por API. Hoy tenés
   MEXC (futuros sin API de órdenes) y Binance. OKX, KuCoin o Bitget (con tu residencia argentina)
   abrirían las mejores combinaciones. No abras nada todavía: primero que el paper lo confirme.

## Normativa y controles investigados (24-oct-2026; verificar antes de operar, cambia seguido)
- **Restricción cruzada** (BCRA, Com. "A" 8336, septiembre 2025; no se encontró norma de 2026 que la
  derogue): quien compra dólar **oficial** no puede operar **MEP ni CCL** por 90 días, y al revés
  (declaración jurada). **No hay tope de monto** para el oficial. Consecuencia: elegir UNA vía
  (oficial o MEP) y sostenerla; no mezclarlas dentro de 90 días.
- **MEP por broker**: comprar AL30 en pesos y vender AL30D; pide declarar que no operaste oficial en los
  últimos 90 días; puede haber "parking". **IOL tiene API oficial** (invertironline.com/api, token
  Bearer; hay que habilitarla en la cuenta) para comprar y vender por programa. PPI: solo cliente no oficial.
- **Binance + Coinag** (enero 2026): transferencias locales en dólares desde bancos argentinos que se
  convierten a USDC "sin comisiones". CriptoYa cotiza esa conversión a ~1,055 USD por USDC (spread de
  ~5,5% incluido en el precio): verificar en la app con una prueba chica.
- **Controles de ARCA** (régimen de información): bancos, billeteras y PSAV informan acreditaciones y
  saldos mensuales **por encima de 50 M ARS** por entidad para personas humanas; desde **2,5 M ARS** por
  transferencia se informa el CBU/CVU de origen y destino; extracciones en efectivo desde 10 M ARS.
  Informar no es sancionar, pero coincide con los límites que fijó el dueño (AR < 50 M ARS/mes y
  < 2,5 M ARS por operación), que cualquier automatización debe respetar como límites duros. Además los
  fondos deben ser justificables con tus ingresos y las ganancias tributan.

## Cálculo con la normativa aplicada (radar del 24-oct, 23 h)
| vía | compra de dólares | dólares → stablecoin | neto por vuelta | automatizable |
|---|---|---|---|---|
| Oficial + Binance P2P en USD | banco (manual) | P2P 1,8% (manual) | ~+4,8% | aviso + venta por API |
| MEP por API de IOL + Binance P2P en USD | IOL API | P2P 1,8% (manual) | ~+3,1% | compra de MEP y venta por API |
| MEP por API de IOL + Binance/Coinag | IOL API | ~5,5% de spread | ~−0,6% | casi todo, pero no rinde |

## Costos reales de Binance vistos por el dueño (24-oct-2026, 100 USD desde cuenta en dólares argentina)
| método | USDT recibidos | USD por USDT | costo | rulo con oficial (1.550), venta en Binance por API | con MEP (1.563) |
|---|---|---|---|---|---|
| Transferencia bancaria (Coinag) | 94,85 | 1,0543 | 5,43% | −0,10% | −0,96% |
| Tarjeta de débito en USD | 97,82 | 1,0223 | 2,23% | **+3,03%** | +2,14% |
| Binance P2P pagando en USD (radar) | — | 1,018 | 1,80% | **+3,46%** | +2,57% |
| Wise P2P / AirTM P2P (requiere saldo en Wise/AirTM) | 98,62 / 98,43 | 1,014 / 1,016 | 1,4% / 1,6% | +3,9% / +3,7% | +3,0% / +2,8% |

EUR → USDT en Binance: 100 EUR → 109,58 USDT (mercado ~1,12): costo ~2,2%. Sirve para fondear desde N26,
no cierra un rulo por sí solo.

Conclusión: la vía 100% automática (MEP por API + Coinag) **no rinde** (el costo de Coinag es real). La que
rinde y es casi toda rápida es **oficial + tarjeta de débito en USD + venta del USDT por API: ~+3% por vuelta**
(o ~+3,5% con P2P en USD). Próximo paso: prueba chica de punta a punta (50-100 USD) para confirmar que el
banco permite la compra con tarjeta en Binance y el costo final real.

## Cómo se decide pasar a dinero real
Igual que el resto del sistema: evidencia en paper que cumpla R-15, luego 20 € reales una semana,
luego más capital. El orden probable: (1) diferencia de funding, (5) préstamo, (2) si se verifica.
