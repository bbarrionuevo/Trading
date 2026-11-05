# Deslistados de Binance: cuánto y cuándo cae el perpetuo (4-nov-2026)

Pedido del dueño: "quiero que podamos operar más para ganar más plata". Binance avisa que deja de operar monedas
("Binance Will Delist ABC, DEF on 2026-11-20") unas 2 veces por mes, con ~2 monedas por aviso. El agente recibe ese
aviso por el canal oficial de Binance en ~1 s. Se midió qué pasa con el perpetuo de esas monedas.

## Datos y método

- 446 avisos de la categoría "Delisting" del sitio de Binance desde febrero de 2022, con su hora de publicación; 156
  monedas tenían perpetuo en USDT (también los ya cerrados).
- **Segundo a segundo:** las operaciones de cada perpetuo del archivo público de Binance (data.binance.vision; la
  API solo guarda 2 días), desde 5 s antes del aviso hasta 2 min después. Hay datos para 34 monedas de 17 avisos
  "Binance Will Delist" desde 2025.
- Salida con las velas de 1 min y 1 h, stop fijo (con los máximos de cada vela), funding real y 0,2 % de costos.

## Resultados

Caída contra el precio de antes del aviso (mediana): a los 2 s −0,1 %; **a los 5 s −8,2 %**; a los 30 s −9,0 %.
Los bots más rápidos venden entre los 2 y los 5 segundos.

| Corto, stop 20 %, salida a las 4 h | n | Mediana | Media | Positivas |
|---|---|---|---|---|
| entrando a los 2 s | 34 | **+18,2 %** | **+12,8 %** | **79 %** |
| entrando a los 3 s | 34 | +10,8 % | +8,5 % | 74 % |
| entrando a los 5 s | 34 | +6,1 % | +2,6 % | 62 % |
| a los 3 s, solo si todavía no cayó más de 4 % | 20 | +9,5 % | +7,5 % | 70 % |
| a los 5 s, solo si todavía no cayó más de 4 % | 8 | +13,8 % | +11,8 % | 75 % |

Por año (a los 2 s): 2025 media +1,4 % (n 13), 2026 +19,9 % (n 21, 95 % positivas). A los 5 s sin filtro, 2025 dio
−3,0 %: **llegar tarde no sirve**. Con velas de 1 minuto y todos los años (109 monedas), el minuto del aviso cae
−4,4 % (mediana) y después la media sigue a favor del corto (+1 a +7 %), con la mediana cerca de 0.

## La regla (`decision/delistings.py`)

- Apenas llega por el canal oficial un aviso "Binance Will Delist ..." (no los de margen, futuros o pares), para
  cada moneda con perpetuo de cripto en Binance: si el precio **todavía no cayó más de 4 %** desde el minuto del
  aviso, **corto** con **stop fijo de 20 %** y **salida a las 4 h**. Si ya cayó, llegamos tarde: no se entra
  (`ya_cayo`). Pasado 1 minuto, nada.
- Sin IA: el título es inequívoco y la IA tarda ~2 s, justo cuando pasa todo. La regla general de noticias no
  vuelve a entrar con el mismo aviso (`lo_opera_la_regla_de_deslistados`).
- **Arriesga 2 % del saldo** (0,10 veces el saldo de nominal): la muestra es chica (17 avisos) y un año fue flojo.
  Excepción a REGLAS C-4 y A-5, aprobada por el dueño al fusionar.

## Cuidado

- Todavía no se midió la demora real del canal de anuncios hasta la PC (desde que se prendió, Binance no publicó
  avisos). Con el primero, revisar en el simulacro la hora de llegada y el precio de entrada.
- La orden lleva tope de precio (50 bps): si el precio cae más rápido que la orden, no se llena (mejor que vender
  después de la caída).
- Estudio repetible: `estudios/deslistados_binance.py`, `estudios/deslistados.py` y
  `estudios/deslistados_segundos.py` (este último baja ~180 MB del archivo público de Binance).
