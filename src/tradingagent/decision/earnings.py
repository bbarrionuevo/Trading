"""Resultados de empresas operados por su IMPULSO en los perpetuos TradFi de Binance (estudio del 4-nov-2026,
docs/ESTUDIO-RESULTADOS.md; excepción a REGLAS C-4 y A-5, aprobada por el dueño al fusionar su PR).

Evidencia: 88 resultados (8-K con el ítem 2.02) de 36 empresas con perpetuo en Binance, de febrero a noviembre de
2026, con la hora de aceptación de la SEC y las velas de 1 minuto. En el primer minuto pasa solo ~16 % del movimiento
de la hora, y su dirección se da vuelta seguido; desde el minuto 2 el precio sigue. Regla simulada minuto a minuto
sobre TODOS los eventos, con 0,15 % de costos: a los 2 minutos de la aceptación, si el perpetuo ya se movió al menos
0,5 % contra el precio de antes, entrar en esa dirección con stop FIJO de 5 % y salida a los 30 min → n = 60, mediana
+1,01 %, media +1,17 %, 39 de 60 positivas (la peor, −5,2 %).

La dirección la da el precio, no la lectura del texto: el 8-K no trae las estimaciones de los analistas y sin ellas ni
la IA puede saber si el resultado sorprendió. Por eso esta entrada no pasa por la IA ni por el meta-modelo de noticias
(que mide otra cosa): su probabilidad es el acierto medido. El resto del riesgo es el de siempre (tamaño por el
riesgo por operación, topes, frenos, libro y liquidación).
"""
import re

from ..intelligence import vector as vec
from ..intelligence.classifier import Signal
from .policy import Assessment

SOURCE = "sec.edgar.8k"
CATEGORY = "resultados_impulso"
VERSION = "impulso-resultados-1"
CIK = re.compile(r"\((\d{10})\)")
ITEM_202 = re.compile(r"\bItem 2\.02\b")
ENTRY_DELAY_S = 120.0          # se mide el impulso a los 2 minutos de la aceptación del 8-K
MAX_LATE_S = 240.0             # entrar al minuto 5 ya no rinde (estudio): pasado el minuto 4, no se entra
THRESHOLD = 0.005              # movimiento mínimo a los 2 minutos para seguirlo
HIT_RATE = 0.65                # acierto medido (39 de 60): la probabilidad de la entrada
HOLD_MIN = 30
STOP = 0.05


def filing_cik(source: str, title: str, body: str) -> int | None:
    """El CIK de la empresa si la noticia es un 8-K de resultados (ítem 2.02) del canal de la SEC; si no, None.
    Formato del canal: título "8-K - Tesla, Inc. (0001318605) (Filer)", resumen "... Item 2.02: Results of ..."."""
    if source != SOURCE or not ITEM_202.search(body or ""):
        return None
    m = CIK.search(title or "")
    return int(m.group(1)) if m else None


def signal(ticker: str, side: int) -> Signal:
    return Signal(CATEGORY, 0.9 * side, HIT_RATE, (ticker,), HOLD_MIN, ("impulso_resultados",), version=VERSION,
                  certainty=1.0, relevance=10, importance=8, impact=8)


def assessment(ticker: str, side: int, dt_s: float) -> Assessment:
    v = vec.EventVector(C=10.0, P=float(side), I=8.0, M=8.0, D=vec.duration(HOLD_MIN))
    return Assessment(ticker, side, v, dt_s, "publicada", v.alpha, HIT_RATE, ())
