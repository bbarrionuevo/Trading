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
| R1.5 · Simulacro (sin plata) | **Construido.** El operador real completo en la PC, con la misma decisión y el mismo cuerpo de cada orden, pero ejecutado contra el libro real de MEXC en un exchange simulado: ninguna orden sale | Cuando el dueño quiera: no hace falta plata ni la prueba con órdenes |
| R2 · Dinero real con todo el saldo (decisión del dueño, 3-nov) | Sin tope de capital: opera con todo el saldo de la cuenta de futuros (empieza con 100 USDT) y con lo que se gane. Riesgo 1 % del saldo por operación, stop de emergencia en el exchange, frenos del día, la semana y el drawdown. Se mide lo real contra la práctica: precio de entrada, deslizamiento y comisiones | El tablero muestra la evidencia (F-3: operaciones, DSR); no frena |

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

## Simulacro: probar el dinero real sin plata

Pedido del dueño (3-nov): "resolve como probar sin dinero real". En la PC, en PowerShell:

```powershell
python -m tradingagent armar-real --simulacro --capital 100 --dias 2
```

- No pide frase ni claves: no hay plata en juego. El agente se reinicia solo y avisa por Telegram
  ("🧪 Empecé el SIMULACRO del dinero real...").
- Es el **mismo operador real**: misma noticia, misma IA, mismo venue real (armado, capital, conciliación,
  stop de emergencia, órdenes IOC con tope). Solo cambia el cliente: en vez de mandar la orden a MEXC, la
  ejecuta contra el libro real del momento, con la comisión de taker (0,05 %, conservadora), y guarda el
  cuerpo exacto que hubiera recibido MEXC.
- El "exchange simulado" se guarda en `TRADING_HOME\simulacro\mexc-futuros\exchange-simulado.json`:
  sobrevive a reinicios. También simula el stop de emergencia del exchange.
- Se ve en el tablero ("🧪 Simulacro del dinero real") y se publica en la rama `pc` (es plata de mentira).
- Para pasar a dinero real: `armar-real --capital ... --dias ...` (con la frase) reemplaza al simulacro.

## Qué se puede operar con 100 USDT (datos en vivo de MEXC, 3-nov)

| Contrato | Mínimo (1 contrato) | Con 100 USDT y el sobre de hoy (1x, neta 30 % = 30 USDT) |
|---|---|---|
| BTC_USDT | 0,0001 BTC ≈ 7,75 USDT | entra (hasta 3 contratos) |
| SOL_USDT | 0,1 SOL ≈ 9,3 USDT | entra (hasta 3 contratos) |
| ETH_USDT | 0,01 ETH ≈ 22,9 USDT | entra (1 contrato) |

Con 100 USDT **no hace falta apalancamiento** para que entren los lotes mínimos. El riesgo por operación
sigue siendo 1 % (1 USDT) con el stop, y los frenos diario (2 %) y de drawdown (8 %) siguen iguales.

**Sin tope de capital** (el dueño, 3-nov: "elimina los topes, siempre se va a trabajar e invertir con todo el
dinero disponible"): el programa opera con **todo el saldo** de la cuenta de futuros. Si gana, el saldo crece
y las operaciones crecen con él, siempre en la misma proporción: cada una arriesga como mucho el 1 % del saldo
(con 100 USDT, 1 USDT; con 1.000, 10). Un depósito o un retiro cambian el capital sin contar como ganancia o
pérdida para los frenos.

## Protecciones con plata real

- La clave con permiso de futuros vive **solo en la PC** (`.env`). En GitHub no va; como mucho, una de solo
  lectura.
- La clave **sin permiso de retiro** (se revisa en MEXC).
- Margen **aislado**: una posición no arrastra a las demás.
- 1x (el sobre), con la liquidación al menos a 3 veces la distancia del stop.
- Riesgo 1 % del capital por operación; freno diario 2 %, semanal, y drawdown 8 % (cierra todo y no vuelve
  a operar hasta revisarlo).
- Cada aviso de Telegram dice "💵 DINERO REAL", "SIMULACRO de dinero real (sin plata)" o "práctica (sin
  plata real)".
- **La IA lee cada noticia antes de entrar** (C-4). Con dinero real, sin IA no se abre nada. Motivo: el 3-nov,
  en la PC, las reglas solas abrieron 3 cortos mal leídos ("Mercado Bitcoin" es un exchange de Brasil, no BTC;
  "los IOU de Liquid vuelven a estar respaldados" no es una mala noticia).
- Si MEXC cierra una posición solo (stop de emergencia) o el dueño la cierra a mano, el programa no manda
  nada: la saca de su cuenta con precio estimado, avisa, y la conciliación vuelve a coincidir.
- Nunca se cierra más de lo que MEXC tiene abierto.

## Apalancamiento

El sobre sigue en 1x (sin apalancamiento). Con 100 USDT ya entran los lotes mínimos de BTC, SOL y ETH (tabla
de arriba), así que no hace falta. El apalancamiento no aumenta la ganancia esperada por unidad de riesgo:
con el mismo stop y el mismo 1 % de riesgo, solo inmoviliza menos margen y acerca la liquidación. Si algún día
se quiere, es `per_trade.max_leverage` en `config/limits.yaml` y su línea base en `src/tradingagent/limits.py`
(lo decide el dueño).

## Cómo dimensiona (corregido el 3-nov)

¼-Kelly dice qué fracción del capital **arriesgar** (lo que se pierde si salta el stop). El nominal es ese
riesgo dividido la distancia al stop. Antes se aplicaba al nominal, y las posiciones salían de 50 a 100
veces más chicas. Ahora manda el más estricto entre Kelly, el 1 % de riesgo y los topes del sobre.

## Lo que tiene que hacer el dueño

1. Fusionar el PR protegido de hoy (simulacro, IA antes de cada entrada, sin tope de capital). La PC se
   actualiza sola en unos minutos.
2. **Hoy, sin plata:** `armar-real --simulacro --capital 100 --dias 2` y mirar los avisos "SIMULACRO".
3. **Mañana, con los 100 USDT en la cuenta de futuros de MEXC:**
   1. `mexc-real-check` (pone órdenes que no se pueden ejecutar y las cancela);
   2. `armar-real` y escribir la frase `ARMO DINERO REAL CON TODO EL SALDO POR 30 DIAS`.
4. En MEXC: la clave **sin** permiso de retiro y el modo de posición en **Cobertura (Hedge)**.
5. Para frenar: `armar-real --desarmar` (no abre nada nuevo) o `cerrar-todo` (cierra todo y desarma).
