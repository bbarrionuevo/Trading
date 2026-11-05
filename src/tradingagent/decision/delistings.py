"""Deslistados de Binance: corto en el perpetuo apenas llega el aviso, si el precio todavía no cayó (estudio del
4-nov-2026, docs/ESTUDIO-DESLISTADOS.md; excepción a REGLAS C-4 y A-5, aprobada por el dueño al fusionar su PR).

Evidencia: los avisos "Binance Will Delist ..." desde 2025 con perpetuo en USDT (34 monedas de 17 avisos), segundo a
segundo con el archivo público de Binance. El precio cae entre los 2 y los 5 segundos del aviso (mediana −8 % a los
5 s). Corto a los 2 s con stop de 20 % y salida a las 4 h: mediana +18 %, media +13 %, 79 % positivas; a los 5 s sin
filtro, media +2,6 % (y en 2025, −3 %): llegar tarde no sirve. Entrando solo si el precio todavía no cayó más de 4 %,
la media queda en +7 a +12 % aunque se llegue a los 3-10 s. Por eso la regla mira el precio, no el reloj.

El título de Binance es inequívoco ("Binance Will Delist ABC, DEF on 2026-11-20"): no hace falta la IA, que tarda ~2 s
y en esos 2 s pasa todo. La probabilidad es el acierto medido. Muestra chica: arriesga 2 % del saldo (no 3 %).
"""
import re

from ..intelligence import vector as vec
from ..intelligence.classifier import Signal
from .policy import Assessment

SOURCES = {"binance.ws", "binance.listados"}        # los avisos oficiales de Binance
CATEGORY = "deslistado_corto"
VERSION = "deslistado-1"
TITLE = re.compile(r"^Binance Will Delist (?P<coins>.+?)(?: on \d{4}-\d{2}-\d{2}.*)?$")
COIN = re.compile(r"^\(?([A-Z0-9]{2,15})\)?$")
MAX_LATE_S = 60.0              # el estudio llega hasta los 30 s; más tarde, nada
MAX_DROP = 0.04                # si ya cayó más que esto desde el aviso, llegamos tarde: no se entra
HIT_RATE = 0.74
HOLD_H = 4
STOP = 0.20


def coins(source: str, title: str) -> tuple[str, ...]:
    """Las monedas de un aviso de deslistado de Binance ("Binance Will Delist ABC, DEF and GHI on 2026-11-20");
    nada si es otro aviso (margen, futuros, pares, votaciones)."""
    m = TITLE.match((title or "").strip()) if source in SOURCES else None
    if not m:
        return ()
    out = []
    for part in re.split(r",|\band\b|&", m.group("coins")):
        c = COIN.match(part.strip())
        if c and c.group(1) not in out:
            out.append(c.group(1))
    return tuple(out)


def signal(asset: str) -> Signal:
    return Signal(CATEGORY, -0.9, HIT_RATE, (asset,), HOLD_H * 60, ("deslistado_binance",), version=VERSION,
                  certainty=1.0, relevance=10, importance=8, impact=8)


def assessment(asset: str, dt_s: float) -> Assessment:
    v = vec.EventVector(C=10.0, P=-1.0, I=8.0, M=8.0, D=vec.duration(HOLD_H * 60))
    return Assessment(asset, -1, v, dt_s, "publicada", v.alpha, HIT_RATE, ())
