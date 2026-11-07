"""Listados de Binance al instante (estudio del 6-nov-2026, `estudios/binance_listados_segundos.py`; excepción a REGLAS
C-4, B-4 y A-5 que aprueba el dueño al fusionar su PR): largo en el perpetuo de Binance apenas llega el aviso oficial
de un listado ("Binance Will List Foo (FOO)" o "Introducing Foo (FOO) on Binance HODLer Airdrops"), sin esperar a la
IA, si llegó a tiempo y el precio todavía no saltó.

El estudio, operación por operación con el archivo público de Binance (66 monedas de 58 avisos desde ene-2025 que ya
tenían perpetuo; la hora del aviso es la del CMS de Binance, con milisegundos):

- El perpetuo salta a los 2,30 s de publicado el aviso (mediana; la mitad central entre 2,15 y 2,58 s). A los 2 s, nada.
- Comprar a los 2 s y salir como los listados de Upbit (stop fijo de 10 %, 15 min): mediana +7,50 %, media +18,58 %,
  77 % positivas (positivo en 2025 y en 2026, en listados y en HODLer). Cada 100 ms cuenta: a los 2,2 s +6,1 %; a los
  2,5 s +2,0 %; a los 3,5 s (lo que tarda la IA) −0,5 %. En la apertura del spot el perpetuo no se mueve: no se opera.
- El canal de anuncios (WebSocket) llegó a Tokio a 1,17 s de publicado un aviso a hora cualquiera y a 2,57 s uno a hora
  redonda: por eso el freno de demora.

Entra si la orden puede salir a ≤ 2,2 s de publicado (MAX_LATE_S) y el precio no subió más de 8 % desde la apertura del
minuto del aviso (a los 3,5 s ya subió +9 % de mediana: sería llegar tarde). Más tarde, lo opera la regla general de
listados, con la IA. Sin la IA y sin las alarmas de B-4 (en el salto el libro se vacía); protege el tope de precio de la
orden (D-2). La IA lee después y, si no confirma, se cierra. Arriesga el 3 % del sobre al stop, como Upbit (0,30 veces
el saldo con el stop de 10 %; el dueño, 6-nov: "igual a la de Upbit" y "armarlo ya para dinero real"). Las monedas
estables (con USD en el ticker) no saltan: afuera. Lo arma el dueño (`armar-real --estrategias listado_upbit,listado_binance`).
"""
import re

from ..intelligence import vector as vec
from ..intelligence.classifier import Signal
from .policy import Assessment

SOURCES = {"binance.ws", "binance.listados"}       # el canal de anuncios y el sondeo del catálogo 48
CATEGORY = "listado_binance"
VERSION = "listado-binance-1"
RULE = "listado_binance_al_instante"
NAME = "listado de Binance"
MAX_LATE_S = 2.2               # de publicado a la orden: a los 2,2 s mediana +6,1 %; a los 2,5 s, +2,0 %
MAX_RISE = 0.08                # desde la apertura del minuto del aviso; a los 3,5 s ya subió +9 % (y la mediana es < 0)
HIT_RATE = 0.77                # comprar a los 2 s, salida a los 15 min: 77 % positivas
HOLD_MIN = 15
# Las mismas reglas de título que el estudio: los HODLer Airdrops o "will list" (también los resultados de las
# votaciones); los lanzamientos de perpetuos y los contratos de entrega no son listados de una moneda. Alpha y
# pre-mercado no estuvieron en el estudio: afuera.
LAUNCH = re.compile(r"futures will (?:launch|list)|perpetual.*will launch|will launch.*perpetual", re.I)
HODLER = re.compile(r"hodler airdrop", re.I)
LISTING = re.compile(r"\bwill list\b", re.I)
NOT_STUDIED = re.compile(r"\balpha\b|pre-market", re.I)
TICKER = re.compile(r"\(([A-Z0-9]{2,15})\)")
MULTIPLIER = re.compile(r"^(1000000|100000|10000|1000|100|1M)(?=[A-Z])")   # 1000CHEEMS → CHEEMS (el universo)
NOT_COINS = {"USDT", "USDC", "FDUSD", "BNB", "BTC", "ETH"}


def coin_key(ticker: str) -> str:
    """El ticker como lo guarda el universo de Binance: sin el multiplicador de las monedas de precio chico."""
    return MULTIPLIER.sub("", str(ticker or "").upper())


def notice(source: str, title: str) -> tuple[str | None, tuple[str, ...]]:
    """(el tipo de aviso, sus monedas) si es un listado de Binance que la regla opera; si no, (None, ())."""
    if source not in SOURCES:
        return None, ()
    t = (title or "").strip()
    if LAUNCH.search(t) or NOT_STUDIED.search(t):
        return None, ()
    kind = "hodler_airdrop" if HODLER.search(t) else "listado_spot" if LISTING.search(t) else None
    if kind is None:
        return None, ()
    out: list[str] = []
    for raw in TICKER.findall(t):
        c = coin_key(raw)
        if c and c not in out and c not in NOT_COINS and "USD" not in c:
            out.append(c)
    return (kind, tuple(out)) if out else (None, ())


def max_rise(kind: str | None) -> float:
    """El freno de precio (el mismo para los dos tipos de aviso de Binance)."""
    return MAX_RISE


def max_late(kind: str | None) -> float:
    """La demora máxima (la misma para los dos tipos de aviso de Binance)."""
    return MAX_LATE_S


def signal(asset: str) -> Signal:
    return Signal(CATEGORY, 0.9, HIT_RATE, (asset,), HOLD_MIN, (RULE,), version=VERSION, certainty=1.0, relevance=10,
                  importance=8, impact=8)


def assessment(asset: str, dt_s: float) -> Assessment:
    v = vec.EventVector(C=10.0, P=1.0, I=8.0, M=8.0, D=vec.duration(HOLD_MIN))
    return Assessment(asset, 1, v, dt_s, "publicada", v.alpha, HIT_RATE, ())
