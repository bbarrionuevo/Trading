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

## Medido al instalarlo (5-nov)

- **Upbit bloquea su sitio de avisos para las IP de AWS** (Cloudflare devuelve 403 desde Tokio; su API de mercados sí
  responde). No se esquiva: el relé no lee Upbit y la ganancia de velocidad en Upbit no existe. Upbit lo sigue leyendo
  la PC.
- Lo que queda: la lista de anuncios de Binance desde otra IP (respaldo del canal de la PC) y el túnel listo para lo
  que siga. Con lo empujado sin acelerar y Upbit bloqueado, **la única ganancia grande de velocidad que queda es operar
  desde Tokio** (abajo).

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

## Canal oficial de Upbit en Telegram (5-nov)

- **@upbit_news es el canal oficial** (lo abrió Dunamu, la dueña de Upbit, el 22-ago-2025). Hay otros que copian los
  avisos (@bot_fomo, @upbitlaas): no son de Upbit y no se usan.
- **Medido con 15 avisos de 4 días:** cada uno sale por Telegram 0 a 2 s después del sitio (mediana 1 s). El listado
  de CARDS: sitio 06:03:03 UTC, Telegram 06:03:04; la PC, que sondea cada 8 s, lo recibió a los 7,8 s.
- `relay/telegram_upbit.py` en el servidor de Tokio (Telethon 1.45, extra `relay` del pyproject). Cuenta de un número
  SECUNDARIO del dueño; `TG_API_ID` y `TG_API_HASH` en `/home/ubuntu/.env`; sesión en `~/telegram/upbit.session` (600).
  El canal se fija por su **ID interno** en el primer inicio de sesión; solo entran mensajes de ese ID, con la etiqueta
  **"[거래]"** (trading) y con enlace al sitio de Upbit. El título queda igual al del sitio: si el aviso llega también
  por el lector de la PC, se procesa una sola vez.
- Primer inicio de sesión (interactivo, lo hace el dueño): `cd ~/trading && .venv/bin/python -m
  tradingagent.relay.telegram_upbit --login`. Prueba sin mandar nada: `--escuchar`. Después el servicio `rele-tokio`
  lo arranca solo.

### Por qué reglas exactas y no "palabra + ticker" (Tier-1)

De los 15 avisos del canal, **1 era un listado**; la mayoría traen ticker (ENJ, AKT, ZK, DGB...) y son suspensiones de
depósitos o mantenimientos. Comprar con cualquier ticker y que la IA cierre 1-2 s después no cuesta solo comisiones: en
una suspensión o un deslistado el precio cae en esos segundos (medido en los deslistados de Binance: −8 % a los 5 s).
Las reglas exactas de los títulos de Upbit ("신규 거래지원", "KRW 마켓 디지털 자산 추가", "거래지원 종료", "유의 종목")
son igual de rápidas (microsegundos) y no se equivocan de tipo de aviso. La IA queda como segunda lectura que puede
cerrar (ya existe para deslistados y etiquetas), y todo cierre va con IOC y tope de precio (nunca a mercado sin tope).

### Lo que falta medir antes de entrar a los ~2 s en los listados de Upbit

El estudio de Upbit midió entradas a los 5-30 s (el salto pasa en los primeros 5 s y el primer pico se devuelve en
parte). Entrar a los ~2 s no está medido: se mide segundo a segundo con el archivo público de Binance (como los
deslistados) antes de usarlo con plata real.

### Todo en Tokio (pedido del dueño): por fases

1. Hecho (5-nov): Telegram y la lista de Binance en Tokio → la PC decide (simulacro, plata de mentira).
2. Hecho (5-nov, sección siguiente): el agente COMPLETO corre en Tokio en simulacro (todas las fuentes y estrategias:
   listados, deslistados, etiquetas, perpetuos nuevos, resultados de empresas, noticias generales), con telemetría por
   UDP firmado al tablero de la PC. Se mide cuánto gana entrar desde Tokio contra el simulacro de la PC.
3. Dinero real desde Tokio: clave restringida a la IP del servidor, sin retiros, idealmente de una subcuenta con solo
   el capital armado. Cambia REGLAS A-2, A-3, G-1, G-7 y G-8 y suma G-9 (un solo ejecutor): PR protegido, lo aprueba
   y lo arma el dueño (por SSH, con su frase, en el servidor).

## Fase 2: el agente completo en Tokio (5-nov)

**Medido desde el servidor de Tokio el 5-nov** (con el código de `main`):

| Prueba | Resultado |
|---|---|
| Claude (`llm-check`: la misma llamada que hace el agente, Haiku 5.5) | responde en 1,8 s, 0,0003 USD |
| Binance futuros público (hora, libro de BTC), conexión tibia | 2-3 ms (la primera, 17 ms); desde la PC ~320 ms |
| Binance firmado con la clave de SOLO LECTURA (comisión de BTCUSDT) | 4-5 ms |
| Fuentes (`sources --check`) | todas OK: SEC 8-K 144 ms, PR Newswire 34 ms, lista de Binance 18 ms, CNBC 195-231 ms; Upbit 403 (llega por Telegram) |
| Memoria (416 MB, sin swap) | 162 MB libres antes del agente; el relé usa 59 MB |

**Qué corre:**

- `rele-tokio` (sin cambios de permisos): el canal oficial de Upbit en Telegram y la lista de Binance, repartidos por
  UDP firmado a la PC **y al agente de Tokio** (127.0.0.1). Pasa la telemetría del agente de Tokio a la PC y copia la
  pausa de la PC al agente de Tokio.
- `agente-tokio` (`deploy/tokio/agente-tokio.service`): `python -m tradingagent run` con `TRADING_HOME=~/.trading`.
  Simulacro armado con 100 USDT de mentira (`armar-real --simulacro --capital 100`, modo local: libro y comisión
  reales, ninguna orden) y la cuenta de práctica, como en la PC. `~/.trading/config.yaml`
  (`deploy/tokio/config.yaml`): sin bot de Telegram (lo usa la PC: dos lectores del mismo bot se pisan), sin leer el
  sitio de Upbit (403) ni la lista de Binance (la lee el relé: una sola lectura por IP).

**Claves en el servidor (nunca se muestran; todas con permisos 600):**

- `~/.env` (lo maneja el dueño): `TG_API_ID`, `TG_API_HASH`, `API_Key_Binance`, `SECRET_Key_Binance`, `API_IA`. El
  relé lee de acá solo las dos de Telegram.
- `~/.trading/.env` (el del agente, armado con `deploy/tokio/preparar_env.py`): `ANTHROPIC_API_KEY` (= `API_IA`),
  `BINANCE_LECTURA_KEY` / `BINANCE_LECTURA_SECRET` (la de solo lectura de la PC: sin restricción de IP, solo lectura;
  pedido del dueño: "usa la que ya tenés") y `SEC_USER_AGENT`, mandadas desde la PC por SSH con
  `deploy/tokio/enviar_claves_pc.py`. **No tiene la clave de trading**: el simulacro no la usa.

**Telemetría al tablero de la PC** (`telemetry/remota.py` → relé → `relay/tablero.py` → `state/tokio.json` y
`state/tokio-eventos.jsonl` → sección "Servidor de Tokio" del tablero):

- Cada 15 s, un resumen: versión, memoria, fuentes, noticias, lag, peso de Binance, IA del día, pausa y cada cuenta
  (equity, PnL, operaciones y posiciones abiertas). Cada alerta (lo que en la PC va a Telegram) apenas sale. El relé
  manda cada 30 s su estado y el del canal de Telegram (conectado, avisos, demora del último).
- Va **firmada pero no cifrada** (HMAC-SHA256, hora ±30 s, número único). Con dinero real no lleva montos: solo el
  retorno en %, los símbolos y el tipo de alerta. Lo que se publica en la rama `pc` no lleva cuentas reales de Tokio.

**Pausa:** el latido de la PC dice si la PC está en pausa (archivo KILL, o /pausa por Telegram). El relé crea entonces
`~/.trading/KILL` en Tokio (marcado como suyo) y lo borra cuando la PC sale de la pausa; uno puesto a mano en el
servidor queda. Llega en ~1 s.

**Actualizar o reinstalar:** con el CI de `main` en verde,
`ssh -i C:\Users\Brian\.ssh\lightsail-tokio.pem ubuntu@176.32.87.26 "bash ~/trading/deploy/tokio/instalar-agente.sh"`
(trae `main`, instala, copia los servicios y reinicia los dos). Ver cómo anda: el tablero de la PC, o
`sudo journalctl -u agente-tokio -n 50` en el servidor.

