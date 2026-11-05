# Cuánto poner en cada operación y cuándo usar apalancamiento (4-nov-2026)

Pedido del dueño: "quiero que aprendas a usar el apalancamiento". Lo que hacen los fondos con mejores resultados:
**el tamaño lo fija el riesgo, no el apalancamiento.** Se decide cuánto del saldo se puede perder si salta el stop
(acá, 3 %), y el tamaño sale de dividir ese riesgo por la distancia al stop. El apalancamiento aparece solo cuando
el stop está cerca: con stop a 1 %, arriesgar 3 % es una posición de 3 veces el saldo; con stop a 5 %, de 0,6.

## Medido con las 60 operaciones de "resultados con impulso" (`docs/ESTUDIO-RESULTADOS.md`)

Cada operación: media +1,17 %, desvío 2,72 %, 1 sola tocó el stop (en ese minuto el precio pasó 0,55 % más allá).
Saldo simulado operación tras operación y 10.000 años de 80 operaciones remuestreadas:

| Tamaño (veces el saldo) | Riesgo al stop | Las 60 operaciones | Caída máxima | Año: mediana | Año: peor 5 % | Años con caída ≥ 8 % |
|---|---|---|---|---|---|---|
| 0,2 | 1 % | ×1,15 | −2,4 % | +20 % | +11 % | 0 % |
| 0,3 (hoy: tope de neta 30 %) | 1,5 % | ×1,23 | −3,5 % | +32 % | +17 % | 0 % |
| 0,4 | 2 % | ×1,32 | −4,7 % | +45 % | +23 % | 1 % |
| **0,6 (3 % aprobado)** | **3 %** | **×1,51** | **−7,0 %** | **+73 %** | **+37 %** | **9 %** |
| 1 | 5 % | ×1,97 | −11,5 % | +149 % | +67 % | 53 % |
| 2 | 10 % | ×3,70 | −22,0 % | +472 % | +160 % | 99 % |
| 3 | 15 % | ×6,63 | −31,7 % | +1.135 % | +297 % | 100 % |

- **Kelly** (el tamaño que más hace crecer el saldo a la larga si los números fueran exactos) da 14,8 veces el
  saldo, y 11 si se rebaja la ganancia media un error estándar. Nadie serio usa Kelly entero: con 60 operaciones el
  acierto puede estar inflado y una brecha (un salto de precio por encima del stop) lo cambia todo. Los fondos usan
  de ¼ de Kelly para abajo y suben a medida que se confirma.
- Con 2 o 3 veces el saldo, la caída de 8 % que frena todo (A-4) llega casi seguro en el año, y cada operación
  arriesga 10 a 15 %: rompe la regla del 3 %.
- **La conclusión:** usar el 3 % aprobado completo (0,6 veces el saldo en esta estrategia). Hoy cada operación
  queda en 0,3 por el tope de exposición neta de 30 %, que se pensó para una cartera que se mantiene, no para una
  operación de 30 minutos.

## Lo que cambió en el código (`decision/policy.py`)

- Cada estrategia tiene su tope de tamaño según su estudio (`ExitProfile.max_x`): resultados 0,6; lanzamientos 0,065
  (2 % al stop de 30 %); listados 0,3 (con
  su stop de 10 % ya arriesgan 3 %); lo que no tiene estudio propio, 0,3 (lo de hoy). El sobre del dueño sigue
  mandando por encima.
- La neta la fija solo el sobre (`config/limits.yaml`); el tope propio de 30 % del código se sacó.

## Cuándo sí usar apalancamiento

- Solo cuando el stop está cerca y la estrategia está probada: con stop a 1 %, arriesgar 3 % es 3 veces el saldo.
  Hoy ninguna estrategia medida lo necesita (resultados 5 %, listados 10 %, lanzamientos 30 % de stop).
- La liquidación tiene que quedar a 3 veces o más la distancia del stop (`min_liq_multiple`): con 2x, la
  liquidación está a ~48 %, y un stop de 30 % no pasa esa prueba. Por eso el sobre sigue en 1x.
- La tendencia diversificada (`docs/ESTUDIO-TENDENCIA.md`) tampoco lo necesitó: a volatilidad de 20 % usa 0,42
  veces el saldo.

## Lo que necesita el OK del dueño (PR protegido)

El sobre de hoy (posición ≤ 40 %, neta ≤ 30 %) no deja usar el 3 % en los resultados. Se propone posición ≤ 60 %,
bruta ≤ 100 %, neta ≤ 60 % y 4 posiciones (antes 3), **sin apalancamiento (1x)**. El riesgo por operación (3 %) y
los frenos de pérdida (2 % por día, 4 % por semana, 8 % en total) no cambian.
