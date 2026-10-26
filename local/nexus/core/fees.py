"""Calculadora de rentabilidad neta. Todo en Decimal para evitar errores de coma flotante."""
from dataclasses import dataclass
from decimal import Decimal as D

HUNDRED = D(100)


def _pct(x) -> D:
    return D(str(x)) / HUNDRED


@dataclass(frozen=True)
class CostModel:
    buy_fee_pct: D            # comisión exchange al comprar (maker/taker)
    sell_fee_pct: D           # comisión exchange al vender
    p2p_spread_pct: D         # spread P2P estimado
    network_fee_eur: D        # gas/retiro fijo
    bank_transfer_eur: D      # costo bancario fijo (SEPA, etc.)

    @classmethod
    def from_config(cls, cfg: dict, venue: str, order: str = "taker",
                    withdrawal: str = "USDT_TRC20") -> "CostModel":
        f = cfg["fees"]
        key = f"{order}_pct"
        return cls(
            buy_fee_pct=_pct(f[venue][key]),
            sell_fee_pct=_pct(f[venue][key]),
            p2p_spread_pct=_pct(f["p2p_spread_pct_default"]),
            network_fee_eur=D(str(f["withdrawal_fixed_eur"][withdrawal])),
            bank_transfer_eur=D(str(f["sepa_transfer_eur"])),
        )


@dataclass(frozen=True)
class NetResult:
    capital_in: D
    capital_out: D
    net_profit: D
    net_roi_pct: D
    gross_roi_pct: D
    total_costs: D
    executable: bool


def net_profit(capital_eur, buy_price, sell_price, cost: CostModel,
               min_net_roi_pct=0.0) -> NetResult:
    """Ciclo: EUR -> activo (buy_price) -> EUR (sell_price), restando todos los costos.

    buy_price/sell_price deben estar en la misma moneda (EUR por unidad de activo).
    """
    cap, bp, sp = D(str(capital_eur)), D(str(buy_price)), D(str(sell_price))
    if cap <= 0 or bp <= 0 or sp <= 0:
        raise ValueError("capital y precios deben ser > 0")

    qty = cap * (1 - cost.buy_fee_pct) / bp
    gross = qty * sp
    after_fees = gross * (1 - cost.sell_fee_pct) * (1 - cost.p2p_spread_pct)
    out = after_fees - cost.network_fee_eur - cost.bank_transfer_eur

    profit = out - cap
    roi = profit / cap * HUNDRED
    gross_roi = (sp / bp - 1) * HUNDRED
    return NetResult(cap, out, profit, roi, gross_roi, cap * sp / bp - out,
                     roi > D(str(min_net_roi_pct)))


def breakeven_spread_pct(capital_eur, cost: CostModel) -> D:
    """Spread bruto mínimo (%) para ROI neto = 0 con este capital."""
    cap = D(str(capital_eur))
    k = (1 - cost.buy_fee_pct) * (1 - cost.sell_fee_pct) * (1 - cost.p2p_spread_pct)
    fixed = (cost.network_fee_eur + cost.bank_transfer_eur) / cap
    return ((1 + fixed) / k - 1) * HUNDRED
