# Tendencia y "momentum entre activos" en los perpetuos de cripto de Binance (4-nov-2026)

Pregunta del dueño: "¿qué hacen los que tienen los mejores resultados?". La estrategia más usada por los fondos
sistemáticos grandes (los CTA) es **seguir la tendencia en muchos mercados a la vez**: largo lo que viene subiendo,
corto lo que viene bajando, cada posición chica y del mismo riesgo, y la cartera a una volatilidad fija. Se probó
con los datos de Binance.

## Datos y método

- 114 perpetuos de cripto que existían antes de 2022, **incluidos los que Binance ya cerró** (para no quedarse solo
  con los que sobrevivieron). Velas diarias desde 2020 y el funding real de cada día.
- Solo se opera un contrato si mueve ≥ 20 M USDT por día (promedio de 30 días).
- Costos: 0,08 % por cada unidad operada (comisión de tomador y deslizamiento).
- Se eligió con 2020-2022 y se juzga con **2023 en adelante** (lo que el método no vio).

## Resultados (por año; Sh = rendimiento sobre riesgo; DD = peor caída)

| Estrategia | 2020-2022 | 2023 en adelante |
|---|---|---|
| BTC comprar y mantener | +32 %, Sh 0,76, DD −77 % | +51 %, Sh 1,12, DD −53 % |
| Tendencia 20 días, volatilidad 20 % | +16 %, Sh 0,72, DD −20 % | +18 %, Sh 0,82, DD −29 % |
| **Tendencia 30 días** | **+32 %, Sh 1,27, DD −11 %** | **+16 %, Sh 0,78, DD −24 %** |
| Tendencia 30 días, ajuste semanal | +29 %, Sh 1,13, DD −13 % | +34 %, Sh 1,32, DD −14 % |
| Tendencia 60 días | +20 %, Sh 0,84, DD −22 % | +16 %, Sh 0,76, DD −21 % |
| Tendencia 90 / 120 días | −11 % / +22 % | +2 % / −6 % |
| Tendencia 30 días, solo los 3 más fuertes | +22 %, Sh 0,93, DD −30 % | −6 %, Sh −0,16, DD −45 % |
| Tendencia 30 días, los 10 más fuertes | +30 %, Sh 1,20, DD −23 % | +8 %, Sh 0,46, DD −29 % |
| Tendencia 30 días, solo largos | +33 %, Sh 1,32, DD −24 % | +5 %, Sh 0,33, DD −45 % |
| Entre activos (largo lo que más subió, corto lo que menos), 1 a 28 días | −20 % a +8 % | −14 % a +1 % |

Tendencia 30 días por año: 2020 +45 %, 2021 +51 %, 2022 +4 %, 2023 +35 %, 2024 +20 %, 2025 +5 %, 2026 +6 %.

## Lo que dice

- **La tendencia funciona, pero solo diversificada:** con 20 a 60 días gana en los dos períodos y con caídas
  mucho menores que BTC; concentrada en 3 a 10 contratos pierde o casi no gana. Hay que estar en 35 a 90
  contratos a la vez, cada uno chico.
- El apalancamiento no hizo falta: con volatilidad de 20 % la cartera usa en promedio 0,42 veces el saldo (la
  cripto ya es muy volátil). Con tope 1x, 2x o 3x da lo mismo.
- **El "momentum entre activos" no funciona** fuera de muestra (ni seguir al que más subió ni ir contra él).
- Los últimos dos años ganó menos (+5 % y +6 %).

## Por qué todavía no se opera

- **Capital:** 35 a 90 posiciones de ~0,5 a 1 % del saldo cada una necesitan ~1.000 a 2.000 USDT para que cada
  orden supere el mínimo de Binance (5 USDT; BTC 50, ETH 20). Con 100 USDT no se puede armar.
- **Frenos:** sus caídas normales (−14 % a −24 % con volatilidad de 20 %) pasan el freno total de 8 % (A-4). Con
  volatilidad de 10 % la caída fue −13 %. Para operarla hace falta un presupuesto de caída propio, que decide el
  dueño.
- Queda como la primera estrategia para cuando el capital crezca. Para repetir el estudio:
  `python estudios/historia_binance.py <carpeta>` baja los datos y `python estudios/tendencia.py
  <carpeta>/tendencia.json` lo corre.
