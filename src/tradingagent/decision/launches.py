"""Perpetuos NUEVOS de cripto en Binance: corto a los 15 minutos del inicio si el precio ya quedó en o debajo del
primero (estudio del 4-nov-2026, docs/ESTUDIO-LANZAMIENTOS.md; excepción a REGLAS C-4 y A-5, aprobada por el dueño
al fusionar su PR).

Evidencia: 521 lanzamientos de perpetuos de cripto en USDT desde 2023, con velas de 1 minuto (primeras 16 h) y de 1 h
(7 días), el funding real y 0,3 % de costos. Ir corto a todos rinde poco (media +1,1 % a las 72 h con stop de 30 %) y
a los que suben fuerte en los primeros 15 minutos les sigue subiendo (más de +30 %: mediana −19 % para el corto).
Los que a los 15 minutos ya quedaron en o debajo del primer precio siguen bajando: n = 250, mediana +7,4 %, media
+4,2 %, 62 % positivas, ganancia media +21,5 % y pérdida media −23,5 %; por año +3,1 % (2024), +7,5 % (2025), +2,4 %
(2026) y −0,6 % (2023). Es más débil que los resultados de empresas: arriesga 2 % del saldo (no 3 %).

La dirección la da el estudio, no la lectura de un texto: no hay noticia que leer (el contrato empieza a operar y
listo). Por eso no pasa por la IA ni por el meta-modelo de noticias: su probabilidad es el acierto medido.
"""
from ..intelligence import vector as vec
from ..intelligence.classifier import Signal
from .policy import Assessment

SOURCE = "binance.lanzamientos"
CATEGORY = "lanzamiento_corto"
VERSION = "lanzamiento-1"
ENTRY_DELAY_S = 900.0          # se decide a los 15 minutos del inicio
MAX_LATE_S = 1200.0            # pasados 20 minutos ya no es lo que se midió: no se entra
MAX_RISE = 0.0                 # solo si el precio quedó en o debajo del primero (lo que se midió)
HIT_RATE = 0.62                # 154 de 250
WIN_LOSS = 0.91                # ganancia media / pérdida media (21,5 % / 23,5 %)
HOLD_H = 72
STOP = 0.30


def new_contracts(catalog: dict, now: float, seen=()) -> list[tuple[str, float]]:
    """(contrato, inicio en segundos) de los perpetuos de cripto en USDT que empezaron a operar hace menos de 20
    minutos y todavía no se vieron. Solo nombres ASCII (Binance también lista monedas con nombre en chino)."""
    out = []
    for sym, c in (catalog or {}).items():
        t0 = float(c.get("alta") or 0)
        if (c.get("tipo") == "PERPETUAL" and c.get("subyacente", "COIN") == "COIN" and c.get("trading")
                and sym.isascii() and sym not in seen and 0 <= now - t0 <= MAX_LATE_S):
            out.append((sym, t0))
    return sorted(out, key=lambda x: x[1])


def event(symbol: str, t0: float, now: float) -> dict:
    """El evento que se registra: la regla se dispara a los 15 minutos (esa es su hora)."""
    eid = f"lanzamiento-{symbol}-{int(t0)}"
    return {"id": eid, "title": f"Binance Futures empezó a operar {symbol} (perpetuo nuevo)", "body": "",
            "snippet": "", "source": SOURCE, "tier": 1, "cluster": eid, "cluster_sources": 1,
            "published_at": now, "received_at": now, "assets_hint": [symbol[:-4]], "inicio": t0}


def signal(asset: str) -> Signal:
    return Signal(CATEGORY, -0.9, HIT_RATE, (asset,), HOLD_H * 60, ("lanzamiento",), version=VERSION,
                  certainty=1.0, relevance=10, importance=7, impact=7)


def assessment(asset: str, dt_s: float) -> Assessment:
    v = vec.EventVector(C=10.0, P=-1.0, I=7.0, M=7.0, D=vec.duration(HOLD_H * 60))
    return Assessment(asset, -1, v, dt_s, "lanzamiento", v.alpha, HIT_RATE, ())
