# Dirección del proyecto: estrategias que se apagan solas, no un adivino de mercado

Decidido el 25-oct-2026 a pedido del dueño ("quiero delegarte la dirección del proyecto"), para evitar
el sobreajuste a datos pasados y no depender de que una sola estrategia sirva para todo.

## Principios
1. **Medir el clima, no predecirlo.** Nada de un clasificador que adivine "lateral o tendencial" y salte
   a otra estrategia: detecta tarde, zigzaguea en las transiciones y es un sobreajuste en sí mismo. Lo
   direccional ya se probó sin ventaja.
2. **Cada estrategia mide en tiempo real la condición que necesita y se apaga sola cuando falta**, con
   pocas medidas y con sentido económico:
   - market making: volatilidad baja del par (filtro), sin caída fuerte de BTC, pausa si el par pierde;
   - diferencia de funding: que sus patas sigan cobrando (últimas 24 h);
   - carry: funding positivo sostenido; cobertura: inventario y brecha disponibles.
3. **No operar es una estrategia.** La plata sin uso va a préstamo de stablecoins (Bitfinex, Aave,
   Morpho: ~6-11% anual). Apagarse no es perder.
4. **Asignador de capital** entre las estrategias activas según su desempeño reciente, con topes por
   estrategia, para que nada dependa de una sola.

## Disciplina contra el sobreajuste
- **Grupo de control**: el laboratorio corre la misma estrategia sin el filtro sobre los mismos datos
  (estrategia `control`). Un filtro solo se queda si gana al control **hacia adelante**.
- **Evidencia por versión** (`strategy_tag`): una versión nueva no hereda la evidencia de otra.
- **Clima en cada vuelta**: volatilidad, tendencia (efficiency ratio) y BTC a 15 min, en categorías
  fijas (no ajustadas a los datos). El informe diario muestra el resultado por clima, vigente y control.
- **Validación día por día**: un umbral se elige con datos de ayer y se prueba hoy; si sólo funciona en
  un rango finísimo, se descarta.
- **Reglas para muchos pares**, no para una moneda: los pares entran y salen solos (selección y pausa).

## Etapas
1. (25-oct) Registro de clima + grupo de control + informe por clima. Sin cambiar la operación.
   (25-oct) Modelo de llenado 3: la orden se llena solo si el volumen cubre la cola Y la orden
   entera. El modelo 2 inventaba vueltas en pares de un tick de spread con colas largas.
2. (25-oct) Asignador de pares del laboratorio (`nexus/allocator.py`): conserva los pares con cota
   inferior positiva y explora con el resto; cada vuelta queda marcada "promovido"/"explorando" al
   entrar, así la selección se mide fuera de muestra. Con 3-5 días de datos: condiciones de cada
   estrategia definidas con evidencia hacia adelante.
3. Si "promovido" le gana a "explorando" fuera de muestra: los libros operados pasan a ser los mejores
   promovidos. Asignador entre estrategias y destino por defecto en préstamo de stablecoins.
4. Dinero real según R-15: 20 € una semana, luego 100 €, sumando pares y exchanges.
