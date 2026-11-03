"""Liquidez y toxicidad del flujo antes de entrar (informe, sección de microestructura).

Cuando sale una noticia, los creadores de mercado retiran órdenes: el spread se abre, la profundidad se
evapora y el flujo se vuelve tóxico (contrapartes informadas). Antes de cada entrada se mide:
- spread relativo y su z-score contra la media reciente (EWMA): z > 2,5 ⇒ no entrar;
- L3 = suma de los 3 primeros niveles de compra Y de venta (en USDT): por debajo de k × la orden ⇒ no;
- divergencia entre el último precio operado y el medio del libro > 1 % ⇒ libro congelado: no;
- lambda de Kyle (ΔP = λ·Q + ε) con velas de 1 minuto que traen el volumen comprador agresivo: si el
  λ de los últimos minutos se dispara contra el de la hora previa, el mercado castiga cada orden.
"""
import math
from dataclasses import asdict, dataclass

from .book import OrderBook

MIN = 60_000


@dataclass
class SpreadStats:
    """Media y varianza exponenciales del spread relativo (bps) de un par."""
    mean: float = 0.0
    var: float = 0.0
    n: int = 0
    ts: float = 0.0

    def update(self, spread_bps: float, ts: float, half_life_n: float = 24.0) -> None:
        if self.n == 0:
            self.mean, self.var = spread_bps, 0.0
        else:
            a = 1 - 2 ** (-1 / half_life_n)
            d = spread_bps - self.mean
            self.mean += a * d
            self.var = (1 - a) * (self.var + a * d * d)
        self.n += 1
        self.ts = ts

    def z(self, spread_bps: float, min_n: int = 12) -> float | None:
        """z-score del spread actual; None si todavía no hay historia suficiente."""
        if self.n < min_n:
            return None
        sd = max(math.sqrt(self.var), 0.25 * self.mean, 0.1)       # piso: spreads de un tick no dan z infinitos
        return (spread_bps - self.mean) / sd

    @classmethod
    def from_dict(cls, d: dict | None) -> "SpreadStats":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})

    def as_dict(self) -> dict:
        return {k: round(v, 6) if isinstance(v, float) else v for k, v in asdict(self).items()}


def l3_notional(book: OrderBook) -> float:
    """L3: nocional de los 3 primeros niveles de los dos lados del libro."""
    return float(sum(p * q for p, q in book.bids[:3]) + sum(p * q for p, q in book.asks[:3]))


def divergence(book: OrderBook, last: float | None) -> float | None:
    """|último − medio| / medio, o None sin último precio."""
    if last is None or book.mid is None or book.mid <= 0:
        return None
    mid = float(book.mid)
    return abs(last - mid) / mid


def kyle_lambda(candles: dict, start_ms: int, end_ms: int) -> float | None:
    """λ de Kyle por mínimos cuadrados (sin constante): retorno de cada minuto contra el flujo neto agresivo
    (compras agresivas − ventas agresivas, en USDT). Necesita velas con volumen y volumen comprador."""
    xs, ys = [], []
    for m in range(start_ms // MIN * MIN, end_ms // MIN * MIN, MIN):
        k = candles.get(m)
        if not k or len(k) < 6 or k[4] is None or k[5] is None or k[0] <= 0:
            continue
        q = 2 * k[5] - k[4]                                         # comprador agresivo − vendedor agresivo
        xs.append(q)
        ys.append(math.log(k[3] / k[0]))
    sxx = sum(x * x for x in xs)
    if len(xs) < 8 or sxx <= 0:
        return None
    return sum(x * y for x, y in zip(xs, ys, strict=True)) / sxx


def kyle_ratio(candles: dict, end_ms: int, recent_min: int = 10, base_min: int = 50) -> float | None:
    """λ de los últimos `recent_min` minutos sobre el de los `base_min` anteriores (None sin datos)."""
    end = end_ms // MIN * MIN
    recent = kyle_lambda(candles, end - recent_min * MIN, end)
    base = kyle_lambda(candles, end - (recent_min + base_min) * MIN, end - recent_min * MIN)
    if recent is None or base is None or base <= 0:
        return None
    return recent / base
