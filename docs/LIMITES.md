# Límites de riesgo para 100 € (derivación)

**Ningún límite garantiza ganar.** Los límites acotan cuánto se puede perder. La ganancia tiene que
venir de una estrategia con esperanza positiva, demostrada antes en paper (docs/REGLAS.md, R-15).
El sistema protege la idea de que "siempre haya ganancias" de dos maneras: corta las pérdidas
rápido y **apaga automáticamente una estrategia que deja de ganar**.

| Límite | Valor | Por qué |
|---|---|---|
| Posición máxima | 40 € | Dos posiciones como máximo; si una se traba, la otra sigue operando |
| Exposición total | 80 € | Deja 20 € de colchón para comisiones y deslizamiento |
| Stop por posición | 1% (≈0,43 € con 40 € y comisión taker real de MEXC de 0,08%) | Una vuelta perdedora cuesta como ~3–5 vueltas ganadoras de spread fino |
| Pérdida diaria | 2 € (2%) | ≈4 stops; un mal día no se come la semana |
| Pérdida semanal | 4 € (4%) | Dos días malos en la semana ⇒ pausa hasta el lunes |
| Drawdown total | 8 € (8%) | **Halt permanente**: solo vuelve si lo rearmás vos (`python -m nexus.agent.risk --rearmar`) |
| Stops seguidos | 3, luego 60 min de pausa | Detecta régimen adverso (tendencia fuerte) |
| Ventana de estrategia perdedora | 7 días | PnL neto de 7 días < 0 ⇒ la estrategia se apaga |
| Órdenes por minuto | 30 | Debajo de los límites de la API del exchange |
| Tenencia máxima | 30 min | Una posición trabada no queda abierta indefinidamente |

**Peor caso acotado:** 8 € de pérdida total (8%) antes de que el sistema se detenga solo y pida tu
decisión. Bajar cualquiera de estos valores está permitido. Subirlos es categoría C: el CI lo rechaza
y la línea base solo la cambia el dueño.

**Cuándo revisarlos:** tras 4 semanas de paper, con la ganancia media diaria observada `g`, la
pérdida diaria debería cumplir `límite diario ≤ 3·g`. Así un mal día se recupera en ≤ 3 días buenos.
Si `g` resulta menor que 0,67 €/día, la estrategia no justifica el riesgo con este capital.
