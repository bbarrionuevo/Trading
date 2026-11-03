"""Política de decisión (determinista): de una señal a un plan de entrada, o a los motivos para no entrar.

La IA no decide (REGLAS C-3): esta capa toma la señal y aplica las reglas en orden. Si algo falla, no se
opera. Por noticia y activo:
1. `news_blocks` · la noticia: categoría operable, edad ≤ `news.max_age_s` del sobre (B-2), una sola
   operación por historia (B-3) y corroboración para el nivel 3 (B-1).
2. `assess` · el evento (informe "Trading Sistemático por Eventos"): vector C/P/I/M/D; obsoleta si
   Δt > 360 s; C ≥ 7; |α(Δt)| ≥ α_min con vida media de 120 s; el lado sale del signo de α (corto solo
   donde se puede); meta-modelo p = P(TP antes que SL) ≥ 0,60.
3. `plan` · el tamaño: triple barrera por σ (SL 1,5σ, TP 3σ, b = 2), costos (comisión, spread e impacto
   por raíz cuadrada), b neto de costos, ¼-Kelly f = ¼·(p·b − (1−p))/b, paridad de volatilidad (pérdida al
   stop ≤ `per_trade.max_risk_pct`, A-5) y topes del sobre: posición, apalancamiento, exposición bruta y
   exposición neta direccional (30 % del equity, del informe).
4. `market_blocks` · el libro real del momento (B-4 y microestructura): spread del sobre y su z-score
   (≤ 2,5), profundidad a 50 bps, L3 de los dos lados, divergencia último/medio (≤ 1 %) y lambda de Kyle.
"""
import math
from dataclasses import dataclass, field
from decimal import Decimal as D

from ..execution.costs import best_schedule, round_trip, sqrt_impact
from ..intelligence import vector as vec
from ..intelligence.classifier import NOT_TRADABLE, Signal
from ..intelligence.meta import MetaModel
from ..marketdata.book import OrderBook
from ..marketdata.liquidity import divergence, l3_notional
from .barriers import BarrierConfig, barriers

SIDE_NAME = {1: "largo", -1: "corto"}


@dataclass(frozen=True)
class PolicyConfig:
    c_min: float = 7.0
    alpha_min: float = 0.10
    half_life_s: float = vec.HALF_LIFE_S
    p_min: float = 0.60
    kelly_fraction: float = 0.25
    barrier: BarrierConfig = field(default_factory=BarrierConfig)
    max_net_exposure_pct: float = 30.0      # del informe; el sobre puede ser más estricto
    max_spread_z: float = 2.5
    l3_multiple: float = 5.0
    max_divergence: float = 0.01
    max_basis: float = 0.01                 # futuros: el libro, a ≤ 1 % del precio de marca (investigación nueva)
    max_kyle_ratio: float = 4.0
    depth_band_bps: float = 50
    impact_y: float = 0.6
    max_slippage_bps: float = 30            # tope de precio de la entrada (≤ per_trade.max_slippage_bps)
    cash_buffer: float = 0.99               # spot: deja 1 % para comisiones
    min_liq_multiple: float = 3.0           # futuros: la liquidación, a ≥ 3 veces la distancia del stop
    exits: dict = field(default_factory=lambda: dict(EXITS))   # salida propia por categoría (medida con datos)


@dataclass(frozen=True)
class Assessment:
    asset: str
    side: int                               # +1 largo, -1 corto, 0 sin lado
    vector: vec.EventVector
    dt_s: float
    dt_basis: str
    alpha: float                            # α(Δt), ya decaída
    p: float
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"activo": self.asset, "lado": SIDE_NAME.get(self.side, ""), "vector": self.vector.as_dict(),
                "dt_s": round(self.dt_s, 1), "dt_base": self.dt_basis, "alfa": round(self.alpha, 4),
                "p": round(self.p, 4)}


@dataclass(frozen=True)
class EntryPlan:
    symbol: str
    side: int
    notional: D
    sl_frac: float
    tp_frac: float
    horizon_s: int
    p: float
    alpha: float
    kelly: float                            # fracción de ¼-Kelly sobre el equity (antes de los topes)
    cost: float                             # ida y vuelta, fracción del nocional
    b_net: float
    risk_usd: float                         # pérdida si toca el stop, costos incluidos
    sigma_1m: float
    impact: float
    slices: int = 1
    trail: float = 0.0                      # > 0: stop que sigue al precio a esa distancia (sin objetivo fijo)

    def as_dict(self) -> dict:
        return {"lado": SIDE_NAME[self.side], "notional": str(self.notional), "sl": round(self.sl_frac, 5),
                "tp": round(self.tp_frac, 5), "horizonte_s": self.horizon_s, "p": round(self.p, 4),
                "alfa": round(self.alpha, 4), "kelly": round(self.kelly, 4), "costo": round(self.cost, 5),
                "b_neto": round(self.b_net, 3), "riesgo_usd": round(self.risk_usd, 4),
                "sigma_1m": round(self.sigma_1m, 6), "impacto": round(self.impact, 7), "tramos": self.slices,
                "stop_movil": round(self.trail, 4)}


def news_blocks(event: dict, sig: Signal, now: float, limits: dict,
                traded_clusters: set | frozenset = frozenset()) -> list[str]:
    """Motivos para NO actuar sobre esta noticia (lista vacía = se puede evaluar)."""
    out = []
    if sig.category in NOT_TRADABLE:
        out.append(f"categoria_no_operable:{sig.category}")
    t_pub = event.get("published_at")
    age = now - (t_pub if t_pub is not None else event["received_at"])
    if age > limits["news"]["max_age_s"]:
        out.append(f"noticia_vieja:{age:.0f}s")
    if event.get("cluster") in traded_clusters:
        out.append("historia_ya_operada")
    if event.get("tier", 2) >= 3 and event.get("cluster_sources", 1) < limits["news"]["min_sources_unverified"]:
        out.append("nivel3_sin_corroboracion")
    return out


def assess(event: dict, sig: Signal, asset: str, now: float, cfg: PolicyConfig, meta: MetaModel | None = None, *,
           allows_short: bool = True, magnitude: dict | None = None) -> Assessment:
    """Vector, decaimiento y meta-modelo de una noticia para un activo."""
    meta = meta or MetaModel()
    v = vec.vector(sig, asset, event, magnitude)
    dt, basis = vec.age_s(event, now)
    alpha = vec.decay(v.alpha, dt, cfg.half_life_s)
    side = (alpha > 0) - (alpha < 0)
    p = meta.p(alpha, sig.category, SIDE_NAME.get(side, "largo")) if side else 0.0
    reasons = []
    if vec.stale(dt, cfg.half_life_s):
        reasons.append(f"alfa_obsoleta:{dt:.0f}s")
    prof = cfg.exits.get(sig.category)
    if prof is not None and dt > prof.max_age_s:
        reasons.append(f"{sig.category}_tarde:{dt:.0f}s")
    if v.C < cfg.c_min:
        reasons.append(f"relevancia_baja:C={v.C:.0f}")
    if abs(alpha) < cfg.alpha_min:
        reasons.append(f"alfa_baja:{alpha:+.3f}")
    if side < 0 and not allows_short:
        reasons.append("corto_no_disponible")
    if side and p < cfg.p_min:
        reasons.append(f"meta_p_baja:{p:.2f}")
    return Assessment(asset, side, v, dt, basis, alpha, p, tuple(reasons))


def kelly(p: float, b: float, fraction: float = 0.25) -> float:
    """Fracción de Kelly: fraction·(p·b − (1 − p))/b (0 si no hay ventaja)."""
    if b <= 0:
        return 0.0
    return max(0.0, fraction * (p * b - (1 - p)) / b)


def plan(a: Assessment, sig: Signal, symbol: str, *, equity: float, cash: float, gross: float, net: float,
         n_positions: int, price: float, sigma_1m: float | None, adv: float | None, fee_taker: float,
         spread: float, kind: str, max_leverage: float, min_notional: float, limits: dict,
         cfg: PolicyConfig, funding: float = 0.0) -> tuple[EntryPlan | None, list[str]]:
    """Tamaño y barreras (sin el chequeo de libro, que se hace con el libro real al ejecutar). `funding`: lo que
    se pagaría de funding en contra si la posición llega a su expiración (fracción del nominal): es un costo más."""
    pt, pf = limits["per_trade"], limits["portfolio"]
    if n_positions >= pf["max_positions"]:
        return None, ["maximo_de_posiciones"]
    if sigma_1m is None or price <= 0 or equity <= 0:
        return None, ["sin_volatilidad_medible"]
    sl, tp = barriers(sigma_1m, sig.horizon_min, cfg.barrier)
    prof = cfg.exits.get(sig.category)
    if prof is not None:                                    # salida medida para esta categoría
        sl = max(sl, prof.min_stop)
        tp = sl * prof.b_kelly
    sigma_daily = sigma_1m * math.sqrt(1440)
    risk_target = equity * pt["max_risk_pct"] / 100
    lev = min(float(max_leverage), float(pt["max_leverage"]))
    net_cap = equity * min(cfg.max_net_exposure_pct, pf.get("max_net_exposure_pct", cfg.max_net_exposure_pct)) / 100
    caps = [equity * pt["max_position_pct"] / 100, equity * lev,
            equity * pf["max_gross_exposure_pct"] / 100 - gross, net_cap - a.side * net]
    if kind == "spot":
        caps.append(cash * cfg.cash_buffer)
    notional, impact, cost, b_net, f = min(caps), 0.0, 0.0, 0.0, 0.0
    for _ in range(2):                                      # el impacto depende del tamaño: dos pasadas
        impact = sqrt_impact(max(notional, min_notional), adv, sigma_daily, cfg.impact_y)
        cost = round_trip(fee_taker, spread, impact) + max(0.0, funding)
        b_net = (tp - cost) / (sl + cost)
        f = kelly(a.p, b_net, cfg.kelly_fraction)
        # Kelly dice qué fracción del capital ARRIESGAR (lo que se pierde si salta el stop), no el nominal: el
        # nominal es ese riesgo dividido la distancia al stop. Manda el más chico entre Kelly, el 1 % y los topes.
        notional = min(equity * f / (sl + cost), risk_target / (sl + cost), *caps)
    if b_net <= 0:
        return None, [f"costos_mayores_que_el_objetivo:{cost:.4f}"]
    if f <= 0:
        return None, [f"sin_ventaja_neta:p={a.p:.2f},b={b_net:.2f}"]
    if notional < min_notional:
        return None, [f"nominal_menor_al_minimo:{notional:.2f}"]
    edge = a.p * (tp - cost) - (1 - a.p) * (sl + cost)               # ventaja esperada por operación
    urgency = max(edge, 0.0) * price * math.log(2) / (cfg.half_life_s / 60)   # lo que se pierde por minuto de espera
    schedule = best_schedule(notional / price, price, sigma_1m, impact, drift=urgency)
    return EntryPlan(symbol, a.side, D(str(round(notional, 2))), sl, tp, int(sig.horizon_min * 60), a.p, a.alpha, f,
                     cost, b_net, round(notional * (sl + cost), 6), sigma_1m, impact, schedule["tramos"],
                     prof.trail if prof is not None else 0.0), []


def news_move(candles: dict, t_pub: float | None, price_now: float, side: int) -> float | None:
    """Cuánto se movió el precio A FAVOR (+) o EN CONTRA (−) del lado de la noticia desde que se publicó: de la
    apertura del minuto de la publicación al precio de ahora. None sin hora creíble o sin la vela."""
    if t_pub is None or price_now <= 0 or not side:
        return None
    c = candles.get(int(float(t_pub) * 1000) // 60_000 * 60_000)
    if not c or float(c[0]) <= 0:
        return None
    return (price_now / float(c[0]) - 1) * side


# Categorías cuyo efecto sigue después del primer salto (estudio del 3-nov, docs/ESTUDIO-LISTADOS.md: con los
# listados spot de Binance la moneda siguió subiendo en MEXC hasta la hora, mediana +12,5 % a los 5 min y +28 % a
# los 60). Ahí, que ya se haya movido a favor no es motivo para no entrar.
DRIFT = {"listado"}


@dataclass(frozen=True)
class ExitProfile:
    """Salida propia de una categoría, medida con datos reales (docs/ESTUDIO-LISTADOS.md)."""
    min_stop: float                         # el stop nunca más cerca que esto (la moneda estaba quieta antes)
    trail: float                            # stop que sigue al mejor precio a esta distancia, sin objetivo fijo
    b_kelly: float                          # ganancia/pérdida esperada que usa Kelly (no hay objetivo fijo)
    max_age_s: float                        # pasado esto la noticia ya no se opera: el salto ya pasó
    max_slippage_bps: float = 30            # tope de precio de la entrada (nunca más que el del sobre)


# Listados spot: 7 casos reales, entrando al minuto: con stop fijo y objetivo 10 % la media fue +4,9 % por
# operación; con stop que sigue al precio a 5 %, +12,7 % (5 de 7 ganan). Entrando a los 5 minutos, todo pierde:
# solo vale llegar primero.
EXITS = {"listado": ExitProfile(min_stop=0.05, trail=0.05, b_kelly=2.5, max_age_s=120, max_slippage_bps=50)}


def chase_blocks(move: float | None, sl_frac: float, tp_frac: float, category: str = "") -> list[str]:
    """No correr detrás del precio: si desde la noticia ya recorrió el objetivo, la ganancia ya la tomaron otros
    (salvo en las categorías que siguen de largo, `DRIFT`); si fue en contra más que el stop, el mercado no le
    cree a la noticia (o pesa otra cosa)."""
    if move is None:
        return []
    if move >= tp_frac and category not in DRIFT:
        return [f"ya_recorrio_el_objetivo:{move * 100:+.2f}%"]
    if move <= -sl_frac:
        return [f"el_mercado_va_en_contra:{move * 100:+.2f}%"]
    return []


def liquidation_ok(entry: float, side: int, sl: float, leverage: float, mmr: float, cfg: PolicyConfig) -> bool:
    """Futuros con margen aislado: la liquidación tiene que quedar a ≥ `min_liq_multiple` veces el stop."""
    dist = 1 / max(leverage, 1e-9) - mmr                    # distancia a la liquidación, fracción del precio
    return dist >= cfg.min_liq_multiple * sl


def market_blocks(book: OrderBook, notional: D, limits: dict, cfg: PolicyConfig, side: int = 1, *,
                  spread_z: float | None = None, last: float | None = None,
                  kyle_ratio: float | None = None, fair: float | None = None) -> list[str]:
    if book.spread_bps is None:
        return ["libro_vacio"]
    out = []
    if book.spread_bps > D(str(limits["market"]["max_spread_bps"])):
        out.append(f"spread:{float(book.spread_bps):.1f}bps")
    if spread_z is not None and spread_z > cfg.max_spread_z:
        out.append(f"spread_z:{spread_z:.1f}")
    depth = book.depth_notional("buy" if side > 0 else "sell", D(str(cfg.depth_band_bps)))
    if depth < notional * D(str(limits["market"]["min_depth_multiple"])):
        out.append(f"profundidad:{float(depth):.0f}USDT")
    l3 = l3_notional(book)
    if l3 < float(notional) * cfg.l3_multiple:
        out.append(f"l3:{l3:.0f}USDT")
    div = divergence(book, last)
    if div is not None and div > cfg.max_divergence:
        out.append(f"divergencia:{div * 100:.2f}%")
    if kyle_ratio is not None and kyle_ratio > cfg.max_kyle_ratio:
        out.append(f"kyle:{kyle_ratio:.1f}x")
    basis = divergence(book, fair) if fair else None       # el futuro se despegó del precio de marca: una mecha
    if basis is not None and basis > cfg.max_basis:          # o un libro raro; se espera a que vuelva
        out.append(f"base_vs_marca:{basis * 100:.2f}%")
    return out
