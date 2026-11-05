"""Listados de Upbit al instante (estudio del 5-nov-2026, `estudios/upbit_segundos.py`, docs/ESTUDIO-LISTADOS.md;
excepción a REGLAS C-4, B-4 y A-5 que aprueba el dueño al fusionar su PR): largo en el perpetuo de Binance apenas llega
el aviso oficial de Upbit, sin esperar a la IA, si llegó a tiempo y el precio todavía no se escapó.

El estudio, segundo a segundo con el archivo público de Binance (31 listados de Upbit de los últimos 180 días que ya
tenían perpetuo en Binance; spot nuevo y alta en wones), desde el último precio antes del aviso del sitio:

- al segundo, nada (mediana +0,06 %); a los 2 s, +15 % (el tope de precio de Binance en los primeros segundos); a los
  5-9 s, +17,6 a +17,9 %.
- Comprar a los 2 s y salir a los 5 min: mediana +1,45 %, media +4,85 %, 61 % positivas; a los 3 s, +1,85 % y +4,52 %;
  a los 5 s, −1,72 % y +1,40 %; a los 9 s (el sondeo de la PC), −2,12 % y +0,02 %. A los 15 min, desde los 2 s:
  mediana +1,37 %, media +6,62 %, 58 % positivas, peor −10,4 %.

Por eso llega por el canal oficial de Upbit en Telegram (0-2 s después del sitio; relay/telegram_upbit.py) y entra sin
la IA (tarda ~1,8 s, y entre los 3 y los 5 s la mediana pasa de positiva a negativa). Los títulos de Upbit son
inequívocos (las MISMAS reglas que el estudio: research/history.py). La IA lee después y, si no confirma, se cierra
(segunda lectura, como en los deslistados). En el salto el libro se vacía y saltan todas las alarmas de B-4 (spread,
lambda de Kyle, distancia a la marca): no se miran; protege el tope de precio de la orden (D-2). Arriesga el 3 % del
sobre al stop (0,30 veces el saldo con el stop fijo de 10 % de A-5; el dueño, 5-nov: "arriesga el 3%") y sale a los
15 min. Más que eso o apalancamiento, no (estudios/upbit_tamano.py: con 5 % la mitad de las veces se toca el freno de
8 %): lineamiento L-3 de REGLAS.
"""
from ..intelligence import vector as vec
from ..intelligence.classifier import Signal
from ..research.history import UPBIT_KINDS, UPBIT_TICKER
from .policy import Assessment

SOURCES = {"upbit.avisos"}                 # el lector de Upbit y el canal oficial de Telegram (por el relé)
KINDS = ("listado_spot", "listado_krw")    # listado nuevo y alta en el mercado en wones
CATEGORY = "listado_upbit"
VERSION = "listado-upbit-1"
RULE = "listado_upbit_al_instante"
# Desde la hora del mensaje de Telegram (≈ 1 s después del sitio, en segundos enteros): a los 5 s del sitio la mediana
# ya es negativa. El sondeo del sitio (~8 s) queda afuera: lo opera la regla general de listados, como siempre.
MAX_LATE_S = 3.0
MAX_RISE = 0.16                            # el tope de Binance en los primeros segundos es +15 %: más, llegamos tarde
HIT_RATE = 0.58                            # a los 2 s, salida a los 15 min: 58 % positivas
HOLD_MIN = 15
NOT_COINS = {"KRW", "BTC", "USDT"}         # los mercados del título, no monedas


def notice(source: str, title: str) -> tuple[str | None, tuple[str, ...]]:
    """(el tipo de listado, sus monedas) si es un listado de Upbit; si no, (None, ()). El orden de las reglas es el
    del estudio: una advertencia o un fin de soporte nunca cuentan como listado."""
    if source not in SOURCES:
        return None, ()
    t = (title or "").strip()
    for kind, rx in UPBIT_KINDS:
        if rx.search(t):
            if kind not in KINDS:
                return None, ()
            out: list[str] = []
            for c in UPBIT_TICKER.findall(t):
                if c not in out and c not in NOT_COINS:
                    out.append(c)
            return kind, tuple(out)
    return None, ()


def signal(asset: str) -> Signal:
    return Signal(CATEGORY, 0.9, HIT_RATE, (asset,), HOLD_MIN, (RULE,), version=VERSION, certainty=1.0, relevance=10,
                  importance=8, impact=8)


def assessment(asset: str, dt_s: float) -> Assessment:
    v = vec.EventVector(C=10.0, P=1.0, I=8.0, M=8.0, D=vec.duration(HOLD_MIN))
    return Assessment(asset, 1, v, dt_s, "publicada", v.alpha, HIT_RATE, ())
