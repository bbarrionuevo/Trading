"""Cartera y contabilidad, en Decimal.

- spot: solo largo; comprar gasta caja (precio × cantidad + comisión) y vender la devuelve.
- perp lineal: largo o corto; la caja solo cambia por PnL realizado y comisiones; el no realizado
  entra en el equity con el precio de referencia (mark).
El PnL realizado de cada llenado se informa para el riesgo (pérdida diaria, stops seguidos).
"""
from dataclasses import dataclass
from decimal import Decimal as D

from .instruments import Instrument
from .orders import Side

ZERO = D(0)


def _sign(x: D) -> int:
    return (x > 0) - (x < 0)


@dataclass
class Position:
    symbol: str
    kind: str
    qty: D = ZERO              # con signo: > 0 largo, < 0 corto
    avg_price: D = ZERO
    realized: D = ZERO
    fees: D = ZERO

    def snapshot(self, mark: D | None = None) -> dict:
        d = {"symbol": self.symbol, "qty": str(self.qty), "avg_price": str(self.avg_price),
             "realized": str(self.realized), "fees": str(self.fees)}
        if mark is not None and self.qty:
            d["unrealized"] = str(self.qty * (mark - self.avg_price))
        return d


class Portfolio:
    def __init__(self, cash: D = D("1000"), quote: str = "USDT"):
        self.quote, self.cash, self.initial = quote, D(cash), D(cash)
        self.positions: dict[str, Position] = {}
        self.fees_total = self.realized_total = ZERO

    def position(self, inst: Instrument) -> Position:
        p = self.positions.get(inst.symbol)
        if p is None:
            p = self.positions[inst.symbol] = Position(inst.symbol, inst.kind)
        return p

    def qty(self, symbol: str) -> D:
        p = self.positions.get(symbol)
        return p.qty if p else ZERO

    def apply_fill(self, inst: Instrument, side: Side, qty: D, price: D, fee: D) -> D:
        """Aplica un llenado y devuelve el PnL realizado que produjo (0 si solo abre o agranda)."""
        if qty <= 0 or price <= 0 or fee < 0:
            raise ValueError("llenado inválido")
        p = self.position(inst)
        signed = qty * side.sign
        realized = ZERO
        if p.qty == 0 or _sign(p.qty) == _sign(signed):                 # abre o agranda
            new_qty = p.qty + signed
            p.avg_price = (abs(p.qty) * p.avg_price + qty * price) / abs(new_qty)
            p.qty = new_qty
        else:                                                             # reduce, cierra o da vuelta
            closing = min(qty, abs(p.qty))
            realized = closing * (price - p.avg_price) * _sign(p.qty)
            left = qty - closing
            p.qty += closing * side.sign
            if p.qty == 0:
                p.avg_price = ZERO
            if left > 0:                                                  # dio vuelta: abre al revés
                p.qty, p.avg_price = left * side.sign, price
        if inst.kind == "spot":
            if p.qty < 0:
                raise ValueError("spot no admite corto")
            self.cash += -signed * price - fee
        else:
            self.cash += realized - fee
        p.realized += realized
        p.fees += fee
        self.realized_total += realized
        self.fees_total += fee
        return realized

    def equity(self, marks: dict[str, D]) -> D:
        eq = self.cash
        for p in self.positions.values():
            if not p.qty:
                continue
            m = marks.get(p.symbol, p.avg_price)
            eq += p.qty * m if p.kind == "spot" else p.qty * (m - p.avg_price)
        return eq

    def gross_exposure(self, marks: dict[str, D]) -> D:
        return sum((abs(p.qty) * marks.get(p.symbol, p.avg_price) for p in self.positions.values()), ZERO)

    def snapshot(self, marks: dict[str, D] | None = None) -> dict:
        marks = marks or {}
        return {"cash": str(self.cash), "equity": str(self.equity(marks)), "initial": str(self.initial),
                "realized": str(self.realized_total), "fees": str(self.fees_total),
                "positions": [p.snapshot(marks.get(p.symbol)) for p in self.positions.values() if p.qty]}
