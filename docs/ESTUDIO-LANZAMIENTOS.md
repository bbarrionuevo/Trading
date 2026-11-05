# Perpetuos nuevos de cripto en Binance: ¿qué pasa después del inicio? (4-nov-2026)

Pedido del dueño: "quiero que podamos operar más para ganar más plata". Binance Futures lanza perpetuos nuevos de
cripto todo el tiempo (en 2025, casi 5 por semana; en 2026, ~1 por semana). El estudio chico anterior (n = 9) decía
"saltan y se devuelven". Se midió con todos.

## Datos y método

- **521 lanzamientos** de perpetuos de cripto en USDT desde enero de 2023 (incluidos los que Binance ya cerró), con
  la hora de inicio del catálogo (`onboardDate`), velas de 1 minuto de las primeras 16 h, de 1 hora hasta 7 días y
  el funding real de esos días.
- Entrada al cierre del minuto 1, 5, 15 o 60; salida por stop fijo (con los máximos de cada vela y 0,2 % de
  deslizamiento) o por tiempo; 0,3 % de costos de ida y vuelta (el libro es fino al principio).

## Resultados

| Corto, stop 30 % | 4 h | 24 h | 72 h | 7 días |
|---|---|---|---|---|
| entra al minuto 5 | media −1,1 % | −0,3 % | +0,4 % | +2,0 % |
| entra al minuto 15 | −0,4 % | +0,6 % | **+1,1 %** (mediana +4,0 %, 56 % positivas) | +2,5 % |
| entra al minuto 60 | −0,5 % | +0,8 % | +1,0 % | +2,2 % |

- **Ir largo pierde** en todas las variantes (mediana −9 % a −18 % a las 72 h-7 días).
- **Ir corto a todos rinde poco** para el riesgo (media +1,1 % con stop de 30 %), y en 2026 da cero.
- Lo que separa: **cómo se movió en los primeros 15 minutos** (corto al minuto 15, stop 30 %, 72 h):

| A los 15 min, contra el primer precio | n | Mediana | Media | Positivas |
|---|---|---|---|---|
| ya bajó (≤ 0 %) | **250** | **+7,4 %** | **+4,2 %** | **62 %** |
| subió 0 a 10 % | 187 | +2,8 % | −2,2 % | 52 % |
| subió 10 a 30 % | 52 | +4,0 % | +1,2 % | 54 % |
| subió más de 30 % | 32 | −19,0 % | −4,5 % | 44 % |

Los que ya bajaban, por año: 2023 −0,6 % (n 48), 2024 +3,1 % (64), 2025 +7,5 % (109), 2026 +2,4 % (29). Ganancia
media +21,5 %, pérdida media −23,5 % (64 tocaron el stop). Con stop de 50 % los cuatro años dan positivo, pero cada
pérdida es más grande.

## La regla (`decision/launches.py`)

- El operador mira el catálogo de Binance cada minuto. A los **15 minutos** del inicio de un perpetuo de cripto
  nuevo, si el precio quedó **en o debajo del primero**, va **corto** con **stop fijo de 30 %** y **salida a las
  72 h**. Si subió, no entra (queda anotado `no_bajo`).
- **Arriesga 2 % del saldo** (0,065 veces el saldo de nominal), no 3 %: la evidencia es más débil que la de los
  resultados de empresas (un año negativo y la regla se eligió mirando estos mismos datos).
- Sin IA (no hay texto que leer). El libro, los topes, los frenos y la liquidación son los de siempre. Excepción a
  REGLAS C-4 y A-5, aprobada por el dueño al fusionar.

## Cuidado

- El grupo se eligió con los mismos datos: el simulacro y la práctica lo prueban con lanzamientos nuevos; con 60
  operaciones se revisa (F-3). Si 2026 sigue flojo, se apaga.
- Un corto con stop de 30 % puede perder más si el precio salta por encima del stop (en el estudio, 0,2 % más).
- Binance también lista monedas con nombre en chino: no se operan (solo nombres ASCII).
