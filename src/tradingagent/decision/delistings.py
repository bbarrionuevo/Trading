"""Avisos negativos de Binance sobre monedas: corto en el perpetuo apenas llega el aviso, si el precio todavía no cayó
(estudios del 4 y 5-nov-2026, docs/ESTUDIO-DESLISTADOS.md; excepción a REGLAS C-4 y A-5, aprobada por el dueño al
fusionar sus PR). Dos avisos, cada uno con su estudio:

- **Deslistado** ("Binance Will Delist ABC, DEF on 2026-11-20"), 34 monedas de 17 avisos desde 2025, segundo a segundo
  con el archivo público de Binance: el precio cae entre los 2 y los 5 s (mediana −8 % a los 5 s). Corto a los 2 s con
  stop de 20 % y salida a las 4 h: mediana +18 %, media +13 %, 79 % positivas; a los 5 s sin filtro, media +2,6 % (y en
  2025, −3 %). Entrando solo si todavía no cayó más de 4 %, +7 a +12 % aunque se llegue a los 3-10 s.
- **Etiqueta de vigilancia** ("Binance Will Extend the Monitoring Tag to Include ABC, DEF on ..."): el 7-nov, con 53
  monedas de 16 avisos desde mar-2025 (`estudios/hipotesis_direccionales.py vigilancia`, operación por operación), cae
  entre el segundo 2 y el 3 (−0,7 % a los 2 s, −4,1 % a los 3 s) y sigue bajando de a poco (−6,7 % a los 15 min).
  Corto a 1,2 s (el canal de anuncios a hora cualquiera), stop de 10 % y salida a las 4 h: mediana +6,58 %, media
  +6,84 %, 87 % positivas (mitades +5,6 y +8,1 %); a los 2,6 s (hora redonda), +4,28 % y 75 %. Freno de 4 % desde la
  apertura del minuto (con 2 % quedaban afuera 21 de 53 a los 2,6 s) y tope de la orden de 2 % (con 0,5 % se llenaban
  33 de 53 y las que faltaban eran las mejores: por aviso, +4,3 % contra +6,2 %).

Los títulos de Binance son inequívocos: no hace falta la IA, que tarda ~2 s y en esos 2 s pasa todo. La probabilidad es
el acierto medido. Muestras chicas: los deslistados arriesgan 2 % del saldo y la etiqueta de vigilancia 1 % (no 3 %).
El precio cae en segundos: como en los listados al instante, no se miran las alarmas del libro (B-4; quedan anotadas
en la decisión) y protege el tope de la orden. El del deslistado y el de la vigilancia es de 2 % (estudio del 7-nov,
`estudios/hipotesis_direccionales.py`: con 0,5 % se llenaban 33 de 45 y las que quedaban afuera eran las caídas más
rápidas, las mejores; con 2 %, 43, y por aviso +12,3 % en vez de +7,2 %).
"""
import re
from dataclasses import dataclass

from ..intelligence import vector as vec
from ..intelligence.classifier import Signal
from .policy import Assessment

SOURCES = {"binance.ws", "binance.listados"}        # los avisos oficiales de Binance
COIN = re.compile(r"^\(?([A-Z0-9]{2,15})\)?$")
MAX_LATE_S = 60.0              # los estudios llegan hasta los 30 s; más tarde, nada


@dataclass(frozen=True)
class Notice:
    category: str
    title: re.Pattern
    max_drop: float            # si ya cayó más que esto desde el aviso, llegamos tarde: no se entra
    hit_rate: float
    hold_h: float
    version: str
    rule: str


NOTICES = (
    Notice("deslistado_corto", re.compile(r"^Binance Will Delist (?P<coins>.+?)(?: on \d{4}-\d{2}-\d{2}.*)?$"),
           max_drop=0.04, hit_rate=0.74, hold_h=4, version="deslistado-1", rule="deslistado_binance"),
    Notice("vigilancia_corto",
           re.compile(r"^Binance Will Extend the Monitoring Tag to (?:Include )?(?P<coins>.+?)(?: on \d{4}-\d{2}-\d{2}.*)?$"),
           max_drop=0.04, hit_rate=0.87, hold_h=4, version="vigilancia-2", rule="etiqueta_vigilancia_binance"),
)
BY_CATEGORY = {n.category: n for n in NOTICES}
CATEGORY = "deslistado_corto"                      # compatibilidad: el aviso de deslistado
VERSION = "deslistado-1"
MAX_DROP = BY_CATEGORY[CATEGORY].max_drop


def notice(source: str, title: str) -> tuple[Notice | None, tuple[str, ...]]:
    """(el tipo de aviso, sus monedas) si es un aviso de deslistado o de etiqueta de vigilancia de Binance; si no,
    (None, ()). Los de margen, futuros, pares o votaciones no cuentan."""
    if source not in SOURCES:
        return None, ()
    for n in NOTICES:
        m = n.title.match((title or "").strip())
        if m:
            out = []
            for part in re.split(r",|\band\b|&", m.group("coins")):
                c = COIN.match(part.strip())
                if c and c.group(1) not in out:
                    out.append(c.group(1))
            return n, tuple(out)
    return None, ()


def coins(source: str, title: str) -> tuple[str, ...]:
    """Las monedas de un aviso de deslistado de Binance (nada si es otro aviso)."""
    n, out = notice(source, title)
    return out if n is not None and n.category == CATEGORY else ()


def signal(asset: str, category: str = CATEGORY) -> Signal:
    n = BY_CATEGORY[category]
    return Signal(n.category, -0.9, n.hit_rate, (asset,), int(n.hold_h * 60), (n.rule,), version=n.version,
                  certainty=1.0, relevance=10, importance=8, impact=8)


def assessment(asset: str, dt_s: float, category: str = CATEGORY) -> Assessment:
    n = BY_CATEGORY[category]
    v = vec.EventVector(C=10.0, P=-1.0, I=8.0, M=8.0, D=vec.duration(n.hold_h * 60))
    return Assessment(asset, -1, v, dt_s, "publicada", v.alpha, n.hit_rate, ())
