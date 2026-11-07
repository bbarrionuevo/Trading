"""Avisos negativos de Upbit y Bithumb al instante (estudio del 7-nov-2026, `estudios/hipotesis_direccionales.py
advertencias`; excepción a REGLAS C-4 y A-5 que aprueba el dueño al fusionar su PR): corto en el perpetuo de Binance
apenas llega el aviso oficial, sin esperar a la IA, si llegó a tiempo y el precio todavía no cayó más que lo medido.
Dos avisos, cada uno con su estudio (operación por operación con el archivo público de Binance, 0,2 % de costos):

- **Advertencia de Upbit** (유의 종목 지정 o 유의 촉구: la "designación de inversión de riesgo" coordinada por DAXA), 42
  monedas desde ene-2025. No salta: baja de a poco (a los 2 s, −0,09 %; el piso llega a ~1,5 h). Corto a los 2 s, stop
  fijo de 10 % y salida a las 4 h: mediana +2,02 %, media +2,77 %, 69 % positivas (las dos mitades positivas); entrando
  a los 30 s, todavía +1,56 % y +1,72 %. Sin apuro: entra hasta los 30 s si no cayó más de 3 % desde la apertura del
  minuto del aviso (las pocas que ya habían caído más terminaron perdiendo). Las de Bithumb (43) rinden la mitad (+0,90
  %, 53 %) y la mitad vieja casi nada: no se operan.
- **Fin del soporte** (거래지원 종료: el deslistado de Upbit o de Bithumb), 25 monedas desde abr-2025 con el PRIMER aviso
  que llega: 11 los anunciaron los dos a la vez (decisión conjunta) y Bithumb publicó siempre primero o junto (0 a 6 s
  antes). Cae en los primeros segundos (−2,6 % a los 2 s desde la apertura del minuto). Corto a los 2 s, stop fijo de
  10 % y salida a los 5 min: mediana +3,73 %, media +4,28 %, 80 % positivas (mitades +5,1 y +3,5 %); a 1 s, +5,03 %; a
  los 5 s, +3,59 % (76 %); a los 10 s la mediana ya es +0,60 %. Entra si llegó a ≤ 5 s de publicado y no cayó más de
  6 % desde la apertura del minuto. Upbit llega por su canal oficial de Telegram (relay/telegram_upbit.py); Bithumb, por
  la lista de avisos de su web de trading (`bithumb.avisos`, cada 1 s).

Los títulos son de formato fijo y se leen con las MISMAS reglas que el estudio, tramo por tramo ("A 지정 연장, B 거래지원
종료" es la prórroga de A y el fin del soporte de B). Un cambio de fecha (변경) no es un aviso nuevo. La IA lee después y,
si no confirma, se cierra. En el fin del soporte el precio cae en segundos: como en los listados al instante, no se miran
las alarmas del libro (B-4; el estudio mide justamente entrar en esa caída) y protege el tope de la orden (0,5 %: con más
no se llenan más que valgan la pena). La advertencia, que baja de a poco, sí las mira. Muestras chicas: la advertencia
arriesga 2 % del saldo al stop y el fin del soporte 1 % (no 3 %: L-3).
"""
import re
from dataclasses import dataclass

from ..intelligence import vector as vec
from ..intelligence.classifier import Signal
from .policy import Assessment

UPBIT, BITHUMB = "upbit.avisos", "bithumb.avisos"
SOURCES = frozenset({UPBIT, BITHUMB})
# Bithumb solo alimenta estas reglas: sus demás avisos (advertencias, listados, depósitos) no tienen una regla medida y
# la lectura general no los opera (ni gasta IA en ellos).
ONLY_RULES = frozenset({BITHUMB})
SEGMENT = re.compile(r",\s*|\s+및\s+")                # "A, B 및 C": cada tramo puede ser otro aviso
TICKER = re.compile(r"\(([A-Z0-9]{1,15})\)")
NOT_COINS = {"KRW", "BTC", "USDT"}                     # los mercados, no monedas
CHANGE = re.compile(r"변경")                           # cambio de fecha de un aviso anterior: no es nuevo
# El orden del estudio: "지정 해제" (levantada) y "지정 (기간) 연장" (prórroga) también dicen "지정".
KINDS = (("levantada", re.compile(r"지정\s*해제")),
         ("extendida", re.compile(r"지정\s*(?:기간\s*)?연장")),
         ("fin_del_soporte", re.compile(r"거래지원\s*종료")),
         ("advertencia", re.compile(r"유의\s*종목\s*지정(?!\s*기간)|유의\s*촉구|거래유의종목\s*지정(?!\s*(?:연장|해제))")))


@dataclass(frozen=True)
class Rule:
    category: str
    kind: str                  # el tipo de aviso del estudio
    sources: frozenset
    max_late_s: float          # de publicado a la orden
    max_drop: float            # desde la apertura del minuto del aviso: si ya cayó más, llegamos tarde
    hit_rate: float
    hold_min: float
    version: str
    rule: str
    name: str
    jump: bool                 # el precio cae en segundos: sin las alarmas del libro (B-4), como los listados


RULES = (
    Rule("advertencia_upbit", "advertencia", frozenset({UPBIT}), max_late_s=30.0, max_drop=0.03, hit_rate=0.69,
         hold_min=240, version="advertencia-upbit-1", rule="advertencia_upbit", name="advertencia de Upbit",
         jump=False),
    Rule("fin_soporte_corto", "fin_del_soporte", frozenset({UPBIT, BITHUMB}), max_late_s=5.0, max_drop=0.06,
         hit_rate=0.80, hold_min=5, version="fin-soporte-1", rule="fin_del_soporte_coreano",
         name="fin del soporte en Corea", jump=True),
)
BY_CATEGORY = {r.category: r for r in RULES}


def kinds(title: str) -> list[tuple[str, str]]:
    """(tipo, moneda) de cada moneda de un título de Upbit o Bithumb, en el orden del título. Se lee de atrás para
    adelante: un tramo sin tipo ("A(AAA)" en "A(AAA), B(BBB) 거래지원 종료") toma el del que lo sigue."""
    out, kind = [], None
    for seg in reversed(SEGMENT.split(title or "")):
        if CHANGE.search(seg):
            kind = None                                # el cambio de fecha y lo que lo precede: nada
            continue
        kind = next((name for name, rx in KINDS if rx.search(seg)), kind)
        for c in reversed(TICKER.findall(seg)):
            if kind and c not in NOT_COINS and (kind, c) not in out:
                out.append((kind, c))
    return out[::-1]


def notice(source: str, title: str) -> list[tuple[Rule, str]]:
    """(regla, moneda) por cada moneda que opera una regla; nada si es otro aviso u otra fuente."""
    if source not in SOURCES:
        return []
    return [(r, coin) for kind, coin in kinds(title) for r in RULES if r.kind == kind and source in r.sources]


def signal(asset: str, category: str) -> Signal:
    r = BY_CATEGORY[category]
    return Signal(r.category, -0.9, r.hit_rate, (asset,), int(r.hold_min), (r.rule,), version=r.version,
                  certainty=1.0, relevance=10, importance=8, impact=8)


def assessment(asset: str, dt_s: float, category: str) -> Assessment:
    r = BY_CATEGORY[category]
    v = vec.EventVector(C=10.0, P=-1.0, I=8.0, M=8.0, D=vec.duration(r.hold_min))
    return Assessment(asset, -1, v, dt_s, "publicada", v.alpha, r.hit_rate, ())
