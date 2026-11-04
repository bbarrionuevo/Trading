# Estudio: cómo mueven el precio los listados (3-nov-2026)

Pedido del dueño: "buscar en historial para que pueda entender más rápido cómo repercuten las noticias en el
mercado". Se repite en la PC con `python -m tradingagent estudio-listados` (deja el detalle en
`TRADING_HOME\datos\estudios\listados.json`).

## Qué se midió

- **Eventos:** los anuncios oficiales de Binance de los últimos 180 días: "Binance Will List X (FOO)" (listado
  spot) y "Binance Futures Will Launch USDⓈ-Margined FOOUSDT Perpetual" (lanzamiento de futuros).
- **Precio:** la moneda en MEXC futuros, que ya cotizaba antes del anuncio. Velas de 5 minutos (MEXC guarda
  unos 6 meses).
- **Reacción:** retorno desde la apertura de la vela del anuncio (antes de la noticia) hasta los 5, 15, 60 y
  240 minutos, y el pico dentro de las 4 horas.

## Resultados

| | n | 5 min | 15 min | 60 min | 4 h | pico en 4 h |
|---|---|---|---|---|---|---|
| Listado spot | 7 | +12,6 % (6/7 suben) | +20,0 % (6/7) | +28,0 % (5/7) | +4,8 % (4/7) | +39,7 % (7/7) |
| Lanzamiento de futuros | 9 | +5,9 % (8/9) | +2,4 % (5/9) | −0,1 % (4/9) | +7,8 % (5/9) | +22,2 % (9/9) |

(medianas)

- **Listados spot:** las monedas chicas (MarsCoin, Gensyn, Genius, Re) subieron entre 25 y 55 % en los primeros 5
  minutos y **siguieron subiendo hasta la hora**: MarsCoin de +55 % a +66 %, Gensyn de +25 % a +43 %. Las grandes
  y ya conocidas (Hyperliquid, Predict) casi no se movieron.
- **Lanzamientos de futuros:** salto rápido en los primeros minutos que se devuelve. En Predict, +7,2 % al
  minuto y −3,8 % al segundo minuto.

### Cómo salir (las mismas 7 monedas, simulado vela a vela, costos incluidos)

| Salida | Entrando al minuto | Entrando a los 5 minutos |
|---|---|---|
| Stop 5 % y objetivo fijo 10 % (antes) | +4,9 % por operación (5/7 ganan) | 0,0 % (2/7) |
| Stop por volatilidad de antes de la noticia (0,6 % y 1,2 %) | +0,8 % (6/7) | −0,7 % (0/7) |
| **Stop que sigue al precio a 5 % del mejor** | **+12,7 % (5/7)** | −1,4 % (2/7) |

**Llegar primero es todo**: al minuto gana; a los 5 minutos pierde con cualquier salida.

### OKX y Bitget

Listados spot de OKX con contrato en MEXC (n = 4): mediana +0,2 % a 5 min, −0,2 % a 15 min y −0,3 % a 60 min:
**no mueven el precio**. Los anuncios de Bitget son casi todos de acciones. Las dos fuentes quedan apagadas.

### Upbit (4-nov, desde la PC): el efecto más fuerte medido

Su API de avisos solo responde desde la PC, con el almacén de certificados de Windows. Se repite con
`python -m tradingagent estudio-listados --exchange upbit` (detalle en `datos\estudios\listados-upbit.json`). La hora
es la de la primera publicación del aviso. Últimos 180 días, en MEXC futuros (medianas):

| Aviso de Upbit | n | 5 min | 15 min | 60 min | 4 h |
|---|---|---|---|---|---|
| Alta en el mercado en wones ("KRW 마켓 디지털 자산 추가") | 14 | +20,9 % (14/14 suben) | +18,9 % | +29,3 % (14/14) | +12,7 % |
| Listado nuevo ("신규 거래지원") | 21 | +13,5 % (21/21) | +13,3 % | +14,2 % (20/21) | +12,9 % |
| Advertencia levantada ("지정 해제") | 4 | +6,0 % (4/4) | +10,3 % | +3,9 % | +1,1 % |
| Advertencia extendida ("지정 기간 연장") | 6 | +0,2 % (4/6) | +0,5 % | +0,4 % | +0,4 % |
| Advertencia ("유의 종목 지정", "유의 촉구") | 16 | −2,6 % (3/16 suben) | −2,8 % | −3,0 % | −2,2 % |
| Fin del soporte ("거래지원 종료") | 6 | −7,4 % (0/6) | −7,7 % | −7,3 % | −4,4 % |

**Llegar en segundos es todo** (los 7 listados del último mes, con velas de 1 minuto):
- Casi toda la suba pasa dentro del primer minuto. Al cierre del minuto del aviso, RSR ya iba +32 %, PUFFER +74 %,
  CT +29 % y NIL +24 %.
- Entrando recién al cierre del minuto 1, con el stop que sigue al precio a 5 % y 0,2 % de costos: mediana +1,1 %,
  media +4,1 % (4 de 7 ganan). Entrando al minuto 2: mediana +0,3 %.
- Con velas de 1 minuto no se puede medir cuánto queda a los 5 a 20 segundos. Lo va a medir el simulacro con el
  próximo aviso.

Qué cambió en el sistema:
1. Upbit se lee cada **5 s** (antes 10). Cada 2 s, Upbit (Cloudflare) devolvía HTTP 429 en uno de cada tres pedidos.
   En Binance, entrar a los 10–20 s del aviso rinde igual o mejor que a los 5 s: el primer pico se devuelve.
2. La advertencia levantada ya no se lee como deslistado (las reglas abrían un corto contra un +6 %), y la extendida
   no se opera (no es un hecho nuevo).
3. En "KAIA/BTC" BTC es la moneda del par, no el protagonista: antes la noticia del listado de KAIA (+34 % en la
   hora) quedaba también en BTC.

### Upbit operado en Binance Futures, segundo a segundo (4-nov)

Mismos avisos de Upbit, ahora en el lugar donde vamos a operar (Binance USDT-M):
- **Con perpetuo en Binance en ese momento** (`onboardDate`): 18 de los listados nuevos, 15 de las altas en wones y
  11 de los fines de soporte.
- **Velas de 1 minuto:** al cierre del minuto del aviso, +11 % (listado), +18 % (alta en wones) y −6 % (fin del
  soporte).
- **Velas de 1 segundo** (spot de Binance, que acompaña al perpetuo; 21 listados): +23 % a los **5 s**, +19 % a los
  10 s, +16 % a los 30 s y +19 % a los 60 s. El salto es instantáneo y el primer pico se devuelve en parte.

Ganancia neta (0,2 % de costos) según cuándo se entra y cómo se sale, listados (n = 23):

| Entrada | Stop que sigue al precio a 5 % (regla actual) | 15 min con stop fijo de 5 % | **15 min con stop de 10 %** |
|---|---|---|---|
| a los 10 s | mediana −0,2 % (11/23 ganan) | mediana −4,1 % (8/23) | mediana +0,6 %, media +4,3 % (12/23) |
| a los 20 s | mediana −1,0 % (10/23) | mediana −0,2 % (11/23) | **mediana +3,0 %, media +6,6 % (16/23)** |

- Entrar a los 5 s compra el pico (mediana −0,2 % a 15 min); entrar a los 60 s deja poco (+0,8 %).
- El programa entra a los ~6-15 s (Upbit cada 5 s, IA ~2-3 s): justo en la ventana buena.
- **La salida decide:** el sacudón después del pico toca un stop de 5 %; uno de 10 % lo aguanta y captura la
  suba de los minutos siguientes. Con 1 % de riesgo por operación, un stop de 10 % es una posición más chica, no
  más riesgo. **Cambiar el tope de 5 % (REGLAS A-5) para los listados lo decide el dueño.**
- Fin del soporte en corto (n = 8): stop que sigue al precio a 8-10 %, mediana +1,8 % entrando a los 10 s. n chico.
- Los listados propios de Binance casi nunca tienen perpetuo al anunciarse: no se pueden operar en Binance futuros.
- Cuidado: n chico; el spot reemplaza al perpetuo en los segundos; en plena noticia el costo real puede pasar el
  0,2 % (con 0,5 % la mediana a 20 s y 15 min sigue en +2,7 %).

### Binance: la copia del CDN demoraba ~60 s

Medido en la PC el 4-nov: la lista de anuncios de Binance la sirve CloudFront con una copia que dura ~120 s (la
"edad" de la respuesta iba de 10 a 115 s). Leyendo cada 10 s, un listado llegaba en promedio ~60 s tarde, justo el
minuto que decide la ganancia. CloudFront guarda una copia por URL exacta, y el servidor acepta los parámetros en
cualquier orden: ahora se rotan 72 formas equivalentes de la URL, una cada 3 s. Cada forma vuelve a pedirse a los
~216 s, ya vencida, así que la respuesta sale fresca del servidor de Binance. CloudFront solo acepta tamaños de
página 5, 10, 15, 20 y 50; otro valor o un parámetro extra da HTTP 400.

### Bybit (3-nov, desde la PC)

Su API de avisos solo responde desde la PC (desde la nube está bloqueada). Se repite con
`python -m tradingagent estudio-listados --exchange bybit` (detalle en `datos\estudios\listados-bybit.json`).
Últimos 180 días, leyendo todos los avisos (1.831, no vienen en orden estricto):

| | Avisos | Con futuro en MEXC | 5 min | 15 min | 60 min | 4 h | pico en 4 h |
|---|---|---|---|---|---|---|---|
| Listado spot ("Bybit to List Foo (FOO) on Spot") | 10 | 4 | +0,4 % (4/4 suben) | +0,0 % (2/4) | +0,7 % (3/4) | −5,5 % (1/4) | +3,1 % |
| Perpetuo de una cripto (sin los "TradFi", que son acciones) | 192 | 48 | +0,03 % (26/48) | +0,05 % (26/48) | +0,2 % (28/48) | −0,0 % (24/48) | +2,5 % |

(medianas)

- Spot: FOGO +0,1 %, Billions +0,3 %, Arcium +0,5 % (−15,7 % a la hora) y Pons +1,4 % a 5 minutos. Las otras 6
  monedas ni cotizaban en MEXC futuros: no se podrían operar.
- Perpetuos: sin efecto; el pico en 4 h es la volatilidad normal de una moneda chica.
- **Conclusión: Bybit no mueve el precio como Binance (+12,6 % a 5 min). No se suma como fuente**, igual que
  OKX. La prueba de red de la PC lo sigue leyendo por si cambia.

## Qué cambió en el sistema

1. **Los listados no se frenan por "ya recorrió el objetivo"** (`policy.DRIFT`): el impulso sigue alrededor de
   una hora. El freno de "el mercado va en contra" sigue igual.
2. **Los lanzamientos de futuros no se operan:** las reglas no los toman como listado. Con un retraso de 15 a 30
   segundos se compraría el salto que se devuelve.
3. **Monedas chicas en el universo** (≥ 0,1 M USDT por día): las que más subieron movían 0,1 a 0,9 M por día en
   MEXC, debajo del piso de 1 M. Entran, pero su ticker solo cuenta escrito como ticker ("(MARSCOIN)"), como en
   el anuncio oficial. Antes de cada orden se revisan spread y profundidad.
4. **Salida de los listados** (`policy.EXITS`): stop de 5 % que sigue al mejor precio, sin objetivo fijo (el
   stop no se achica aunque la moneda estuviera quieta antes), y solo si la noticia tiene menos de 2 minutos.
5. **Anuncio oficial de Binance cada 10 segundos** (antes llegaba por los medios, minutos tarde). OKX y Bitget,
   apagados: sin efecto medido.

## Cuidado

- n es chico (7 y 9). Es una pista fuerte, no una certeza: el aprendizaje de la PC (`research/learn.py`) sigue
  midiendo cada operación y apaga una categoría si la evidencia sale en contra.
- Las monedas chicas tienen libros finos: el stop puede ejecutarse peor. El riesgo por operación sigue siendo
  1 % del saldo al stop, y los frenos del día y del drawdown siguen iguales.
- Falta: Bithumb (bloquea por país desde la nube; quizá desde la PC sí), un estudio de Upbit (ya es fuente
  desde el PR #22) y los datos macro (Reserva Federal, inflación).
