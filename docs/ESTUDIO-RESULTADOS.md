# Resultados de empresas en los perpetuos TradFi de Binance (4-nov-2026)

Pregunta del dueño: "necesitamos poder invertir en todo (acciones, criptos, monedas, etc)". Binance Futures tiene
235 perpetuos TradFi (acciones de EE. UU., Corea y Hong Kong, ETF, oro, petróleo) que operan las 24 h. ¿Se puede
ganar con los resultados trimestrales de las empresas, si las noticias de los medios llegan 2 a 3 minutos tarde?

## Datos

- **Hora del comunicado:** la de aceptación del 8-K con el ítem 2.02 ("Results of Operations") en la SEC
  (`data.sec.gov/submissions`), desde febrero de 2026 (los perpetuos TradFi empezaron a fines de enero).
- **Precio:** velas de 1 minuto del perpetuo en Binance (`/fapi/v1/klines`), desde el minuto anterior al 8-K hasta
  una hora después.
- **88 resultados de 36 empresas** con perpetuo en Binance: Tesla, Alphabet, Meta, Coinbase, Strategy, Palantir, AMD,
  Intel, Micron, Robinhood, Circle, Broadcom, Oracle, Netflix, Uber, Disney, JPMorgan, Dell, SanDisk, Eli Lilly, IBM,
  Salesforce, ServiceNow, Walmart, Home Depot, Cisco, Qualcomm, Marvell, Rocket Lab, IREN, Western Digital, Visa y
  otras.

## Lo que se vio

- El perpetuo reacciona, pero **despacio**: en el primer minuto pasa solo ~16 % (mediana) del movimiento de la hora.
- La dirección del primer minuto se da vuelta seguido: entrar al minuto 1 no tiene ventaja.
- **Desde el minuto 2 el precio sigue**; al minuto 5 ya es tarde (sin ventaja).

## La regla (simulada minuto a minuto, sobre TODOS los eventos, con 0,15 % de costos)

A los 2 minutos de la aceptación del 8-K, si el perpetuo ya se movió al menos el umbral contra el cierre del minuto
anterior al 8-K, se entra en esa dirección; sale por stop fijo (con los máximos y mínimos de cada minuto) o por tiempo.

| Umbral | Stop | Salida | Operaciones | Mediana | Media | Positivas | Peor |
|---|---|---|---|---|---|---|---|
| 0,5 % | 5 % | 30 min | 60 | **+1,01 %** | +1,17 % | 39 de 60 | −5,2 % |
| 1 % | 5 % | 30 min | 46 | +1,22 % | +1,28 % | 32 de 46 | −5,2 % |
| 0,5 % | 3 % | 30 min | 60 | +0,74 % | +0,77 % | 36 de 60 | −3,1 % |
| 0,5 % | 2 % | 30 min | 60 | +0,50 % | +0,89 % | 34 de 60 | −2,2 % |
| 0,5 % | 5 % | 15 min | 60 | +0,33 % | +0,79 % | 36 de 60 | −5,2 % |
| 0,5 % | 5 % | 60 min | 60 | +0,77 % | +1,15 % | 37 de 60 | −5,2 % |

Todas las variantes ganan después de costos; salir a los 30 min es lo mejor en todas. Se eligió umbral 0,5 % (más
operaciones), stop 5 % y salida a los 30 min.

## Cómo se opera (`decision/earnings.py`)

- La señal llega por el canal de 8-K de la SEC (cada 20 s, 100 por página; demora mediana medida en la PC: ~72 s
  con 392 8-K del 4-nov, leyendo cada 60 s). No hace falta ser el primero: alcanza con llegar antes del minuto 2.
- Probado contra la SEC real el 4-nov (de 16:33 a 17:30 de Nueva York): 56 de los 100 8-K eran de resultados, todos
  reconocidos con su empresa y su ticker. 154 de los 217 perpetuos de acciones de Binance tienen ticker en la SEC; los
  demás son de Corea, Hong Kong o ETF, que no presentan 8-K.
- El CIK del 8-K se pasa a ticker con la lista de la SEC (`marketdata/sec_tickers.py`) y el ticker al perpetuo con el
  universo de Binance. Solo acciones con perpetuo (las demás empresas, nada).
- La probabilidad de la entrada es el acierto medido (65 %); el tamaño, los topes, los frenos, el libro y la
  liquidación son los de siempre. Sin IA: el 8-K no trae las estimaciones de los analistas. Excepción a REGLAS C-4 y
  A-5, aprobada por el dueño al fusionar.

## Límites y próximos pasos

- 88 eventos de tres trimestres: una muestra moderada, y los parámetros se eligieron sobre ella. El simulacro y la
  práctica lo prueban con eventos nuevos; con 60 operaciones se revisa (F-3).
- La hora del 8-K puede llegar unos minutos después del comunicado en algunas empresas (lo publican antes por una
  agencia): la regla usa la del 8-K, que es lo que se midió.
- Las agencias de comunicados (medido desde la PC el 4-nov): PR Newswire responde (CDN de 60 s) y se suma como
  fuente; Business Wire responde pero su CDN guarda 5 min; GlobeNewswire y Accesswire bloquean a los programas. Un
  canal pago o de bróker (Benzinga por Alpaca) traería los titulares con "supera / no alcanza la estimación" en
  segundos.
