"""Libro de órdenes L2 (instantánea) con aritmética Decimal: precios y cantidades exactos.

`walk` recorre los niveles como lo haría una orden que toma liquidez: devuelve cantidad llenada,
costo, precio promedio (VWAP) y peor precio, con tope de precio opcional y una fracción de
participación (no toda la profundidad visible está disponible: otros llegan primero).
"""
from dataclasses import dataclass
from decimal import Decimal as D

ZERO, ONE, BPS = D(0), D(1), D(10000)


class BookError(Exception):
    pass


def dec(x) -> D:
    return x if isinstance(x, D) else D(str(x))


@dataclass(frozen=True, slots=True)
class Walk:
    filled_qty: D
    cost: D                      # suma de precio × cantidad (en moneda de cotización)
    worst_price: D | None
    levels: int
    complete: bool

    @property
    def avg_price(self) -> D | None:
        return self.cost / self.filled_qty if self.filled_qty > 0 else None


@dataclass(frozen=True, slots=True)
class OrderBook:
    venue: str
    symbol: str
    bids: tuple[tuple[D, D], ...]      # de mayor a menor precio
    asks: tuple[tuple[D, D], ...]      # de menor a mayor precio
    ts: float                          # hora del exchange (o de recepción si no la informa)
    received_at: float = 0.0

    @classmethod
    def from_levels(cls, venue, symbol, bids, asks, ts, received_at=None) -> "OrderBook":
        b = tuple(sorted(((dec(p), dec(q)) for p, q in bids if dec(q) > 0), key=lambda x: -x[0]))
        a = tuple(sorted(((dec(p), dec(q)) for p, q in asks if dec(q) > 0), key=lambda x: x[0]))
        return cls(venue, symbol, b, a, float(ts), float(received_at if received_at is not None else ts))

    def problems(self) -> list[str]:
        p = []
        if not self.bids or not self.asks:
            p.append("libro vacío de un lado")
        elif self.bids[0][0] >= self.asks[0][0]:
            p.append(f"libro cruzado: bid {self.bids[0][0]} ≥ ask {self.asks[0][0]}")
        if any(px <= 0 or q <= 0 for px, q in self.bids + self.asks):
            p.append("precio o cantidad no positivos")
        return p

    @property
    def best_bid(self) -> D | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> D | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> D | None:
        if not self.bids or not self.asks:
            return None
        return (self.bids[0][0] + self.asks[0][0]) / 2

    @property
    def spread_bps(self) -> D | None:
        m = self.mid
        if m is None or m <= 0:
            return None
        return (self.asks[0][0] - self.bids[0][0]) / m * BPS

    def age(self, now: float) -> float:
        return now - self.ts

    def walk(self, side: str, qty: D | None = None, notional: D | None = None, limit_price: D | None = None,
             participation: D = ONE) -> Walk:
        """Toma liquidez del lado opuesto. side="buy" consume asks; "sell" consume bids."""
        if (qty is None) == (notional is None):
            raise BookError("indicar qty o notional (uno solo)")
        levels = self.asks if side == "buy" else self.bids
        remaining_q = dec(qty) if qty is not None else None
        remaining_n = dec(notional) if notional is not None else None
        filled = cost = ZERO
        worst, used = None, 0
        for price, avail in levels:
            if limit_price is not None and ((side == "buy" and price > limit_price) or
                                            (side == "sell" and price < limit_price)):
                break
            avail = avail * participation
            if avail <= 0:
                continue
            take = min(avail, remaining_q) if remaining_q is not None else min(avail, remaining_n / price)
            if take <= 0:
                break
            filled += take
            cost += take * price
            worst, used = price, used + 1
            if remaining_q is not None:
                remaining_q -= take
                if remaining_q <= 0:
                    break
            else:
                remaining_n -= take * price
                if remaining_n <= 0:
                    break
        complete = (remaining_q is not None and remaining_q <= 0) or (remaining_n is not None and remaining_n <= 0)
        return Walk(filled, cost, worst, used, complete)

    def depth_notional(self, side: str, within_bps: D) -> D:
        """Notional disponible en el lado que tomaría una orden `side`, dentro de within_bps del mejor precio."""
        levels = self.asks if side == "buy" else self.bids
        if not levels:
            return ZERO
        best = levels[0][0]
        lim = best * (ONE + dec(within_bps) / BPS) if side == "buy" else best * (ONE - dec(within_bps) / BPS)
        return sum((p * q for p, q in levels if (p <= lim if side == "buy" else p >= lim)), ZERO)

    def impact_bps(self, side: str, notional: D) -> D | None:
        """Costo de cruzar el libro con `notional`, en bps contra el medio (None si no alcanza)."""
        w = self.walk(side, notional=dec(notional))
        if not w.complete or self.mid is None:
            return None
        sign = ONE if side == "buy" else -ONE
        return (w.avg_price - self.mid) / self.mid * BPS * sign
