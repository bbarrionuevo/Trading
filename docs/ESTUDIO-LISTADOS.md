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
- Falta: Upbit y Bithumb (bloquean por país desde la nube; quizá desde la PC sí) y los datos macro (Reserva
  Federal, inflación).
