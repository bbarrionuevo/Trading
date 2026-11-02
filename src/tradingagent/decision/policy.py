"""Política de decisión (etapa 3, determinista): de una señal a un plan de entrada o de salida.

La IA no decide (REGLAS C-3): esta capa toma la señal y aplica las reglas en orden. Si algo falla, no
se opera.
- Noticia: categoría operable, convicción mínima, edad ≤ `news.max_age_s` (B-2), una sola operación
  por historia (B-3) y corroboración para el nivel 3 (B-1).
- Mercado (B-4): spread ≤ `market.max_spread_bps` y profundidad dentro de 50 bps ≥
  `market.min_depth_multiple` × la orden.
- Exposición: máximo de posiciones, nocional bruto y nocional por activo.
- Tamaño (A-5): pérdida al stop ≤ `per_trade.max_risk_pct` del equity, escalada por convicción.
  Stop por volatilidad (ATR de velas de 5 min) y take-profit a `reward_risk` veces el riesgo.
En spot solo se puede comprar o vender lo que se tiene: una noticia negativa cierra la posición si la
hay, nunca abre un corto.
"""
from dataclasses import dataclass
from decimal import Decimal as D

from ..intelligence.classifier import NOT_TRADABLE, Signal
from ..marketdata.book import OrderBook

MIN = 60_000


@dataclass(frozen=True)
class PolicyConfig:
    min_conviction: float = 0.30
    atr_mult: float = 2.0                 # stop a 2 ATR de 5 minutos...
    min_stop_bps: float = 60              # ...pero nunca más cerca que 0,6 % (comisiones + ruido)
    max_stop_bps: float = 500             # ni más lejos que 5 %
    reward_risk: float = 2.0
    max_slippage_bps: float = 30          # tope de precio de la entrada (≤ per_trade.max_slippage_bps)
    depth_band_bps: float = 50
    cash_buffer: float = 0.99             # deja 1 % para comisiones


@dataclass(frozen=True)
class EntryPlan:
    symbol: str
    notional: D
    stop_frac: float                      # distancia al stop como fracción del precio
    tp_frac: float
    horizon_s: int
    conviction: float
    risk_usd: float


def news_blocks(event: dict, sig: Signal, now: float, limits: dict, cfg: PolicyConfig,
                traded_clusters: set | frozenset = frozenset()) -> list[str]:
    """Motivos para NO actuar sobre esta noticia (lista vacía = se puede actuar)."""
    out = []
    if sig.category in NOT_TRADABLE:
        out.append(f"categoria_no_operable:{sig.category}")
    if sig.conviction < cfg.min_conviction:
        out.append(f"conviccion_baja:{sig.conviction:.2f}")
    t_pub = event.get("published_at")
    age = now - (t_pub if t_pub is not None else event["received_at"])
    if age > limits["news"]["max_age_s"]:
        out.append(f"noticia_vieja:{age:.0f}s")
    if event.get("cluster") in traded_clusters:
        out.append("historia_ya_operada")
    if event.get("tier", 2) >= 3 and event.get("cluster_sources", 1) < limits["news"]["min_sources_unverified"]:
        out.append("nivel3_sin_corroboracion")
    return out


def atr_5m(candles: dict[int, tuple], end_ms: int, bars: int = 12) -> float | None:
    """ATR de velas de 5 minutos armadas con las de 1 minuto que terminan antes de `end_ms`."""
    start = (end_ms // MIN - bars * 5) * MIN
    agg = []
    for b in range(bars):
        mins = [candles[m] for m in range(start + b * 5 * MIN, start + (b + 1) * 5 * MIN, MIN) if m in candles]
        if len(mins) >= 3:
            agg.append((mins[0][0], max(c[1] for c in mins), min(c[2] for c in mins), mins[-1][3]))
    if len(agg) < bars // 2:
        return None
    trs = [agg[0][1] - agg[0][2]] + [max(h - lo, abs(h - pc), abs(lo - pc))
                                     for (_, h, lo, _), (_, _, _, pc) in zip(agg[1:], agg[:-1], strict=False)]
    return sum(trs) / len(trs)


def market_blocks(book: OrderBook, notional: D, limits: dict, cfg: PolicyConfig, side: str = "buy") -> list[str]:
    if book.spread_bps is None:
        return ["libro_vacio"]
    out = []
    if book.spread_bps > D(str(limits["market"]["max_spread_bps"])):
        out.append(f"spread:{float(book.spread_bps):.1f}bps")
    depth = book.depth_notional(side, D(str(cfg.depth_band_bps)))
    if depth < notional * D(str(limits["market"]["min_depth_multiple"])):
        out.append(f"profundidad:{float(depth):.0f}USDT")
    return out


def plan_entry(sig: Signal, symbol: str, *, equity: float, cash: float, gross: float, n_positions: int,
               price: float, atr: float | None, min_notional: float, limits: dict,
               cfg: PolicyConfig) -> tuple[EntryPlan | None, list[str]]:
    """Plan de compra spot (sin el chequeo de libro, que se hace con el libro real al ejecutar)."""
    pt, pf = limits["per_trade"], limits["portfolio"]
    if n_positions >= pf["max_positions"]:
        return None, ["maximo_de_posiciones"]
    if atr is None or price <= 0:
        return None, ["sin_volatilidad_medible"]
    stop_frac = min(max(cfg.atr_mult * atr / price, cfg.min_stop_bps / 1e4), cfg.max_stop_bps / 1e4)
    scale = 0.5 + 0.5 * min(1.0, max(0.0, (sig.conviction - cfg.min_conviction) / (1 - cfg.min_conviction)))
    risk_usd = equity * pt["max_risk_pct"] / 100 * scale
    notional = min(risk_usd / stop_frac, equity * pt["max_position_pct"] / 100, cash * cfg.cash_buffer,
                   equity * pf["max_gross_exposure_pct"] / 100 - gross)
    if notional < min_notional:
        return None, [f"nominal_menor_al_minimo:{notional:.2f}"]
    return EntryPlan(symbol, D(str(round(notional, 2))), stop_frac, stop_frac * cfg.reward_risk,
                     sig.horizon_min * 60, sig.conviction, round(notional * stop_frac, 4)), []
