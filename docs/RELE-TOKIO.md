# Relé de Tokio (Tier-0): arquitectura y decisiones (5-nov-2026)

Pedido del dueño: un servidor de AWS Lightsail en Tokio (176.32.87.26, ap-northeast-1, UDP 50005) como micro-relé
para quitar la desventaja geográfica y el cuello de botella de la IA. La PC en Argentina sigue siendo la que opera.

## Lo que se midió antes de decidir (desde la PC, 5-nov)

| Medición | Resultado |
|---|---|
| Ida y vuelta PC ↔ servidor de Tokio (TCP) | ~308 ms (≈ 155 ms de ida) |
| Pedido completo PC → Binance Futures (`/fapi/v1/time`, servidor en Asia) | ~320 ms (≈ 160 ms de ida) |
| Conexión PC → borde de Binance, Upbit y Anthropic | 21-27 ms (tienen puntos de entrada cerca; después viaja por su red) |
| Avisos de Upbit desde la PC | ~32 ms (salen de una copia en caché cercana) |
| Canal de anuncios de Binance: de publicado a recibido en la PC | 1,2 a 1,8 s (5 avisos) |
| Agente: de un aviso a la orden (sin red) | ~30 ms (desde el PR #49) |
| `batchOrders` con entrada + stop (demo de Binance) | el stop se rechaza: −4120 "use the Algo Order API" |

## Decisiones

1. **Lo que Binance EMPUJA (canal de anuncios) no se acelera con el relé.** Un aviso cruza el océano una sola vez
   igual: de Binance a la PC (~160 ms) o de Binance a Tokio (~1 ms) y de Tokio a la PC (~155 ms). Para esos el relé
   es un segundo oído (si el canal de la PC se corta), no más velocidad.
2. **Lo que se SONDEA sí se acelera:** Upbit (límite de ~10 pedidos por minuto por IP: la PC lee cada 8 s) y la lista
   de anuncios de Binance. El servidor de Tokio los lee también, desde otra IP y cerca de Corea: con dos lectores de
   8 s en fases al azar, la espera promedio del primero baja de ~4 s a ~2,7 s en Upbit (la señal más fuerte; para los
   listados de Upbit los primeros segundos valen).
3. **El relé manda el AVISO (fuente, título, hora), no "BUY TICKER", y la PC decide.** Las reglas, el riesgo, el
   tamaño y la orden quedan en un solo lugar (la PC, ~30 ms). Entra a la ingesta con la misma fuente y el mismo id
   que el lector de la PC: el primero que llega se procesa y el otro es un duplicado.
4. **Cada datagrama va firmado** (HMAC-SHA256 con una clave de 32 bytes que solo tienen la PC y el servidor, hora con
   ±30 s y número único que no se acepta dos veces). Un "BUY TICKER" crudo lo puede falsificar cualquiera que conozca
   la IP y el puerto, y un latido falso desviaría las señales. El servidor no tiene claves de trading ni de la IA.
5. **El túnel:** la PC manda un latido firmado cada 10 s a Tokio:50005; el servidor guarda de dónde vino (sirve con
   IP dinámica y CGNAT) y responde un "ack" (la PC mide la ida y vuelta). Sin latido en 45 s, no manda nada.
6. **La segunda lectura de la IA (Tier-2) va en la PC.** La IA tarda ~1,9 s en pensar; la red hasta Anthropic es lo
   de menos (22 ms al borde desde la PC). En la PC están la posición y la orden de cierre (con el cierre desde Tokio
   haría falta la clave de trading allá) y la clave de Anthropic no sale de la PC. Ya corre para deslistados y
   etiqueta de vigilancia (PR #50).
7. **`batchOrders` (entrada + stop) no existe más en Binance Futures:** los stops van por el servicio Algo (probado
   en la demo: −4120). El sistema ya hace lo correcto: entrada IOC con tope y, apenas se llena, el stop por Algo.
8. **Tamaño con el mínimo de 5 USDT:** con 100 USDT, 1 % de riesgo y stop de 10 %, la posición es de 10 USDT: pasa el
   mínimo sin apalancamiento. Hoy los listados arriesgan el 3 % aprobado (0,3 veces el saldo).
9. **Control de peso de la API** (el "PR 1" del pedido): hecho en el PR #50 (`marketdata/peso.py`, freno al 80 %).

## Lo único que daría ~0,3 s más en TODO (decisión del dueño)

Mandar la orden desde Tokio y no desde la PC: ahorra la ida al exchange (~160 ms) y el salto Tokio → PC (~155 ms).
Necesita una clave de Binance con permiso de futuros en el servidor: por REGLAS A-3, de una **subcuenta** con solo el
capital armado, **sin retiros** y restringida a la IP de Tokio. Es la ruta protegida `execution/live/`: la decide el
dueño. Mientras tanto, el relé no tiene ninguna clave.

## Instalación

- En el servidor (Ubuntu 22.04, 2 núcleos, 416 MB): `bash deploy/rele-tokio/instalar.sh [rama]` (Python 3.11, el
  repo, el servicio `rele-tokio` de systemd que se reinicia solo; la hora la sincroniza el NTP de Ubuntu). La clave
  `/home/ubuntu/relay.key` la copia la PC por `scp`. Logs: `journalctl -u rele-tokio -f`.
- En la PC: `TRADING_HOME/relay.json` (`{"host": "<IP de Tokio>", "port": 50005}`, fuera del repo) y la misma
  clave en `TRADING_HOME/relay.key`. Con eso el agente arranca el cliente (latido cada 10 s). Llave SSH del
  servidor: `C:\Users\Brian\.ssh\lightsail-tokio.pem` (autorizada el 5-nov).
- Medido desde Tokio (pedido completo con conexión nueva): Binance Futures 48 ms, Upbit 57 ms, lista de anuncios de
  Binance 51 ms, Anthropic 220 ms (está en EE. UU.).
