# Camino a dinero real (futuros de MEXC)

Pedido del dueño (3-nov-2026): "Quiero poder invertir con dinero real lo más pronto posible [...] La idea es
que también puedo usar fondo futuro para el apalancamiento ya que mi inversión no va a ser muy grande."

La idea: llegar a plata real **rápido pero sin apostar a ciegas**. Primero se prueba que la ejecución real
anda bien con muy poca plata. Después se agranda solo con evidencia. Las reglas y el sobre de riesgo los
aprueba el dueño (fusionando el PR protegido de cada etapa, REGLAS G-2).

## Etapas

| Etapa | Qué | Cuándo pasa a la siguiente |
|---|---|---|
| R0 · Práctica (hecho) | La PC opera 24/7 en práctica con precios reales: avisos, tablero, revisión diaria | Ya |
| R1 · Conector real y prueba sin riesgo | **Construido** (PR protegido: lo fusiona el dueño). Probado de punta a punta contra un MEXC falso con órdenes | Cuando `mexc-real-check` da todo OK en la PC y el dueño decide el sobre (abajo) |
| R2 · Calibración con dinero real (F-4) | Hasta 20 USDT de capital, 3x como máximo, riesgo 1 % por operación, stop de emergencia en el exchange. Se mide lo real contra la práctica: precio de entrada, deslizamiento y comisiones | ~20 operaciones reales sin sorpresas de ejecución |
| R3 · Escalar | Con evidencia (F-3: 60 operaciones con DSR ≥ 0,95 entre práctica y real), el capital sube por escalones (×2), nunca por encima del sobre | Cada escalón con su evidencia |

## R1 · Conector real (lo que hay que construir)

- **Cliente firmado de MEXC futuros con una lista cerrada de operaciones:** abrir, cerrar, cancelar,
  consultar órdenes y posiciones, y fijar apalancamiento y margen aislado. **Nunca** retiros ni
  transferencias: el test `test_a3` lo verifica.
- **Venue real** con la misma forma que el de práctica (`submit` / `cancel`). El núcleo de decisión y de
  riesgo es el mismo: la IA propone y el motor decide (C-3).
  - Las entradas son órdenes IOC con tope de precio (deslizamiento máximo).
  - Al entrar se pone un **stop de emergencia en el exchange**, a 1,5 veces la distancia del stop del plan,
    por si la PC se apaga. El agente sale antes con su propio stop.
  - Las salidas son "solo reducir".
- **Conciliación:** cada 30 s se comparan las posiciones del exchange con las del agente. Si no
  coinciden, no se abre nada nuevo y se avisa.
- **`python -m tradingagent mexc-real-check`:** prueba sin riesgo, en la PC.
  1. Lee la cuenta.
  2. Pone una orden "solo maker" de 1 contrato lejísimos del precio. No se puede ejecutar: si cruzara, el
     exchange la rechaza.
  3. Verifica que MEXC la entendió exactamente como se pidió (lado, tamaño, precio, apalancamiento,
     margen aislado) y la cancela.

  Si algo no coincide, el modo real no se arma.
- **Armado local (A-2):** `python -m tradingagent armar-real --capital 20`, en la PC y con confirmación
  escrita.
  - Queda en `TRADING_HOME/ARMADO.json`, junto con el capital máximo y la clave usada.
  - Para desarmar: `armar-real --desarmar`. KILL sigue frenando entradas, y
    `python -m tradingagent cerrar-todo` cierra todo.

## Protecciones con plata real

- La clave con permiso de futuros vive **solo en la PC** (`.env`). En GitHub no va; como mucho, una de solo
  lectura.
- La clave **sin permiso de retiro** (se revisa en MEXC).
- Margen **aislado**: una posición no arrastra a las demás.
- 3x como máximo en la calibración, con la liquidación al menos a 3 veces la distancia del stop.
- Riesgo 1 % del capital por operación; freno diario 2 %, semanal, y drawdown 8 % (cierra todo y no vuelve
  a operar hasta revisarlo).
- Cada aviso de Telegram dice "💵 DINERO REAL" o "práctica (sin plata real)".

## El sobre para una cuenta chica (decisión del dueño, pendiente)

Con el sobre actual (40 % por posición, 30 % de exposición neta, 1x), una cuenta de 20 USDT abre como mucho
~6 USDT por posición. El contrato mínimo de BTC (0,0001 BTC, ~10 USDT) **no entra**, así que con 20 USDT
reales el programa no va a poder operar BTC.

Propuesta (la decide el dueño):

- `max_leverage` 3;
- `max_position_pct` 150;
- `max_gross_exposure_pct` 200;
- `max_net_exposure_pct` 150, en `config/limits.yaml` y en la línea base de `src/tradingagent/limits.py`.

El riesgo no sube: 1 % por operación con el stop, freno diario 2 %, semanal 4 % y drawdown 8 %. El
apalancamiento no aumenta la ganancia esperada por unidad de riesgo. Sirve para que entren los lotes
mínimos y para no inmovilizar toda la cuenta.

Como afloja el sobre, Claude no lo puede cambiar solo: el sistema de permisos lo frenó, y está bien que sea
así. Lo hace el dueño o se lo pide a Claude explícitamente.

## Cómo dimensiona (corregido el 3-nov)

¼-Kelly dice qué fracción del capital **arriesgar** (lo que se pierde si salta el stop). El nominal es ese
riesgo dividido la distancia al stop. Antes se aplicaba al nominal, y las posiciones salían de 50 a 100
veces más chicas. Ahora manda el más estricto entre Kelly, el 1 % de riesgo y los topes del sobre.

## Lo que tiene que hacer el dueño

1. **Ya:** en GitHub → Settings → Secrets and variables → Actions, borrar `ACCESS_KEY_MEXC` y
   `SECRET_KEY_MEXC`. La clave que puede operar no debe estar en GitHub (el repo es público). El recolector
   sigue andando sin ellas.
2. En MEXC, confirmar que la clave **no** tiene permiso de retiro.
3. Para que Claude lea la documentación oficial de MEXC: permitir `www.mexc.com` y `mexcdevelop.github.io`
   en la red del entorno (menú del entorno → Edit → Allowed domains).
4. Cuando esté el PR de R1: fusionarlo y correr en la PC `python -m tradingagent mexc-real-check`. El
   resultado también queda en la rama `pc`.
5. Pasar a la cuenta de futuros el monto de calibración (≤ 20 USDT) y armar.
