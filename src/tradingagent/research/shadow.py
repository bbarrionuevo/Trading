"""Operador paper "como si fuera real", por corridas cortas (el cron de GitHub Actions o la PC).

Una cuenta simulada por mercado, cada una con su estado en `paper/<mercado>/` de la carpeta de datos:
- `binance-spot`: libros, velas y filtros públicos de Binance spot (data-api.binance.vision); solo largos.
- `mexc-futuros` (futures.py): perpetuos USDT-M de MEXC, largos y cortos a 1x, con funding y liquidación.

En cada corrida y por mercado, en este orden:
1. Lo que el exchange habría hecho solo entre corridas, vela por vela de 1 minuto (triple barrera):
   stop-loss, take-profit y expiración (decision/barriers.py). En futuros, además funding y liquidación.
2. Salidas del agente: una noticia relevante en contra de una posición abierta. Salir siempre se puede.
3. Frenos (REGLAS A-4) con el equity marcado a mercado. El freno diario y el halt además cierran todo
   (informe: "cierre total de posiciones y apagado operativo").
4. Entradas, con el núcleo de decisión (decision/policy.py): noticia → vector y alfa → meta-modelo →
   triple barrera, costos y ¼-Kelly → libro real (spread y su z, profundidad, L3, divergencia, Kyle) →
   orden a mercado con tope por ExecutionService (la única puerta, D-0) y PaperVenue (latencia,
   profundidad, tope).
5. Registro: `operaciones/AAAA-MM.jsonl` (F-2: "paper"), `decisiones/AAAA-MM.jsonl` con el análisis
   completo de cada noticia (F-1) y `estado.json`.
Un archivo `KILL` en la raíz de la carpeta de datos frena las entradas de todos los mercados. La versión
de la estrategia (`strategy_tag`) tiene su propia evidencia: con 30 operaciones y DSR < 0,80 se descarta
sola y no abre más (F-3).
"""
import asyncio
import json
import logging
import time
from collections import Counter
from decimal import Decimal as D
from pathlib import Path

import aiohttp

from .. import paths
from ..alerts.model import asset_es, circuit_breaker_alert, entry_alert, esc, exit_alert, ops_es, system_alert, usdt_es
from ..core.bus import EventBus
from ..decision import barriers as bar
from ..decision.policy import (
    SIDE_NAME,
    PolicyConfig,
    assess,
    chase_blocks,
    liquidation_ok,
    market_blocks,
    news_blocks,
    news_move,
    plan,
)
from ..decision.risk import RiskState
from ..execution.instruments import Instrument, InstrumentRegistry
from ..execution.orders import EXCHANGE_FLAT, OrderIntent, OrderType, Side
from ..execution.paper import PaperConfig, PaperVenue
from ..execution.portfolio import Portfolio, Position
from ..execution.service import ExecutionService
from ..intelligence.classifier import VERSION as NLP_VERSION
from ..intelligence.meta import MetaModel
from ..marketdata.liquidity import SpreadStats, kyle_ratio
from ..marketdata.providers import MarketDataError, RestBookProvider
from . import evaluation
from .labeler import MIN, CandleStore, minute

log = logging.getLogger("tradingagent.paper")
DIR, STATE, TRADES, DECISIONS = "paper", "estado.json", "operaciones", "decisiones"
STRATEGY_TAG = f"{NLP_VERSION}+riesgo-2"
BINANCE = "https://data-api.binance.vision/api/v3"
STOP_SLIPPAGE_BPS = 20                              # stops y expiraciones entre corridas: salen tomando liquidez
EXIT_SLIPPAGE_BPS = 50                              # tope de las salidas a mercado del agente (≤ sobre)
INFO_TTL_S = 86_400
MAX_GAP_MIN = 48 * 60                               # velas hacia atrás como máximo si las corridas se cortaron
CORE = ("BTC", "ETH", "SOL")
EMERGENCY_STOP_MULT = 1.5      # stop de emergencia en el exchange (dinero real): 1,5 × la distancia del stop del plan                        # se mide su spread en cada corrida (historia del z-score)


def _month(ts: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts))


def _append(path: Path, rows: list[dict]) -> None:
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _all_trades(base: Path) -> list[dict]:
    return [r for f in sorted((base / TRADES).glob("*.jsonl")) for r in _read_rows(f)] if (base / TRADES).exists() else []


async def get_json(session, url: str, params: dict, what: str):
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                raise MarketDataError(f"{what}: HTTP {r.status}")
            return await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise MarketDataError(f"{what}: {type(e).__name__}") from e


# -- mercados --------------------------------------------------------------------------------------
def parse_symbol_info(s: dict) -> dict:
    """Filtros de un par de exchangeInfo de Binance: tick, lote, cantidad mínima y nominal mínimo."""
    f = {x["filterType"]: x for x in s.get("filters", [])}
    notional = f.get("NOTIONAL", {}).get("minNotional") or f.get("MIN_NOTIONAL", {}).get("minNotional") or "5"
    return {"base": s["baseAsset"], "quote": s["quoteAsset"], "tick": f["PRICE_FILTER"]["tickSize"],
            "step": f["LOT_SIZE"]["stepSize"], "min_qty": f["LOT_SIZE"]["minQty"], "min_notional": notional,
            "trading": s.get("status") == "TRADING"}


class BinanceSpot:
    """Binance spot con datos públicos (sin claves): solo largos, comisión estándar de 0,10 %."""
    name, kind, allows_short, label = "binance-spot", "spot", False, "PAPER spot Binance"
    fee_bps = D("10")

    def __init__(self, urls: dict | None = None, candle_urls: dict | None = None):
        self.urls = {"depth": f"{BINANCE}/depth", "info": f"{BINANCE}/exchangeInfo",
                     "ticker": f"{BINANCE}/ticker/24hr", **(urls or {})}
        self.candle_urls = candle_urls

    def symbol(self, asset: str) -> str:
        return f"{asset}USDT"

    async def instruments(self, session, symbols, cache: dict, now: float) -> InstrumentRegistry:
        for sym in symbols:
            if sym in cache and now - cache[sym].get("ts", 0) < INFO_TTL_S:
                continue
            try:
                data = await get_json(session, self.urls["info"], {"symbol": sym}, f"exchangeInfo {sym}")
                cache[sym] = {**parse_symbol_info(data["symbols"][0]), "ts": round(now)}
            except (MarketDataError, KeyError, IndexError, TypeError) as e:   # sin filtros, ese par no se opera
                log.warning("sin filtros de %s: %s", sym, e)
        reg = InstrumentRegistry(())
        for sym, f in cache.items():
            if f.get("trading", True):
                reg.add(Instrument(sym, "binance", f["base"], f["quote"], tick_size=D(f["tick"]).normalize(),
                                   step_size=D(f["step"]).normalize(), min_qty=D(f["min_qty"]),
                                   min_notional=D(f["min_notional"]), maker_fee_bps=self.fee_bps,
                                   taker_fee_bps=self.fee_bps))
        return reg

    def candle_store(self, session) -> CandleStore:
        return CandleStore(session, urls=self.candle_urls)

    def books(self, session, clock):
        return RestBookProvider("binance", session, clock, base_url=self.urls["depth"], depth=100, max_rps=5)

    async def ticker(self, session, sym: str) -> dict:
        """Último precio operado y volumen de 24 h en USDT."""
        d = await get_json(session, self.urls["ticker"], {"symbol": sym}, f"ticker {sym}")
        return {"last": float(d["lastPrice"]), "adv": float(d["quoteVolume"])}

    def mmr_for(self, sym: str) -> float:
        return 0.0

    async def on_entry(self, run, sym: str, meta: dict) -> None:
        """Spot: sin funding ni liquidación."""

    async def settle(self, run, sym: str, meta: dict, until_ms: int) -> None:
        """Spot: nada que cobrar entre corridas."""


# -- una corrida -------------------------------------------------------------------------------------
def _portfolio(st: dict, market) -> Portfolio:
    pf = Portfolio(cash=D(st["initial"]))
    pf.cash, pf.fees_total, pf.realized_total = D(st["cash"]), D(st.get("fees", "0")), D(st.get("realized", "0"))
    for sym, p in st["positions"].items():
        pf.positions[sym] = Position(sym, "spot" if market.kind == "spot" else "perp", qty=D(p["qty"]),
                                     avg_price=D(p["avg_price"]))
    return pf


def new_state(capital: float, now: float, market) -> dict:
    return {"mercado": market.name, "initial": str(capital), "cash": str(capital), "positions": {}, "risk": {},
            "clusters": [], "instruments": {}, "spreads": {}, "last_check": now, "created_at": round(now),
            "strategy_tag": STRATEGY_TAG, "stats": {"operaciones": 0, "ganadas": 0, "perdidas": 0, "pnl_neto": "0"}}


class _Run:
    """Una corrida de un mercado: comparte libros, velas, cartera, riesgo y registros."""

    def __init__(self, market, st, pf, registry, service, books, store, clock, limits, tg, now, cfg, meta, session):
        self.market, self.st, self.pf, self.reg, self.service = market, st, pf, registry, service
        self.books, self.store, self.clock, self.limits, self.tg = books, store, clock, limits, tg
        self.now, self.cfg, self.meta, self.session = now, cfg, meta, session
        self.trades, self.decisions, self.counts = [], [], Counter()
        self.risk = RiskState.from_dict(st["risk"])
        self.spreads = {s: SpreadStats.from_dict(d) for s, d in st.get("spreads", {}).items()}

    # -- marcas y exposición ------------------------------------------------------------------------
    def mark(self, sym: str) -> D:
        c = self.store.candles.get(sym) or {}
        last = max((m for m in c if m < minute(self.now)), default=None)
        return D(str(c[last][3])) if last is not None else D(self.st["positions"].get(sym, {}).get("avg_price", "0"))

    def marks(self) -> dict:
        return {s: self.mark(s) for s in self.st["positions"]}

    def equity(self) -> D:
        return self.pf.equity(self.marks())

    def exposure(self) -> tuple[float, float]:
        """(bruta, neta con signo) en USDT."""
        m = self.marks()
        gross = sum(abs(p.qty) * m.get(s, p.avg_price) for s, p in self.pf.positions.items() if p.qty)
        net = sum(p.qty * m.get(s, p.avg_price) for s, p in self.pf.positions.items() if p.qty)
        return float(gross), float(net)

    async def _alert(self, alert) -> None:
        if self.tg is not None:
            await self.tg.send_alert(alert)

    # -- salidas ------------------------------------------------------------------------------------
    async def close(self, sym: str, reason: str, *, price: D | None = None, liquidity: str = "taker", ts=None,
                    why: str = "", theoretical: D | None = None) -> None:
        """Cierra la posición: con `price` es un llenado del exchange entre corridas; sin él, una orden a
        mercado con tope contra el libro real del momento."""
        meta = self.st["positions"][sym]
        side = 1 if meta["side"] == "largo" else -1
        inst = self.reg.get(sym)
        qty = abs(self.pf.qty(sym))
        if qty <= 0:                                                   # registro sin cantidad: se limpia
            self.st["positions"].pop(sym)
            return
        if inst is None:                                               # sin filtros del par: no se puede cerrar,
            self.counts["salida_sin_filtros"] += 1                     # la posición sigue (y su valor también)
            log.warning("no se pudo cerrar %s (%s): el par no tiene filtros del exchange", sym, reason)
            return
        exit_side = Side.SELL if side > 0 else Side.BUY
        if price is not None:
            fee = inst.fee(qty * price, liquidity)
            realized = self.pf.apply_fill(inst, exit_side, qty, price, fee)
            done, avg, fees = qty, price, fee
        else:
            book = None
            try:
                book = await self.books.book(sym)
            except MarketDataError:
                pass
            ref = (book.best_bid if side > 0 else book.best_ask) if book else None
            order, block = await self.service.execute(OrderIntent(
                client_id=f"pp-x-{sym}-{round(self.now)}", symbol=sym, side=exit_side, type=OrderType.MARKET,
                qty=qty, max_slippage_bps=D(EXIT_SLIPPAGE_BPS), reduce_only=True, ref_price=ref,
                tags={"role": "exit", "motivo": reason, "strategy_tag": STRATEGY_TAG}))
            if order is not None and order.reason == EXCHANGE_FLAT:      # dinero real: MEXC ya no la tiene
                stop = meta.get("emergency_stop")
                est = ref or self.mark(sym) or D(meta["avg_price"])
                if stop and ((est <= D(stop)) if side > 0 else (est >= D(stop))):
                    est = D(stop)                                      # la cerró el stop de emergencia
                log.warning("%s ya estaba cerrada en el exchange: se sincroniza a %s (estimado)", sym, est)
                await self.close(sym, "cerrada_en_el_exchange", price=est, why=why, theoretical=est)
                return
            if order is None or not order.filled_qty:
                self.counts["salida_fallida"] += 1
                log.warning("no se pudo cerrar %s: %s", sym, block or (order.reason if order else ""))
                return
            done, avg, fees = order.filled_qty, order.avg_price, order.fees
            realized = sum((f.realized_pnl for f in order.fills), D(0))
            theoretical = theoretical or ref
        share = done / abs(D(meta["qty"]))
        entry_fee = D(meta["entry_fee"]) * share
        funding_paid = D(meta.get("funding_paid", "0")) * share        # futuros: lo cobrado o pagado en el camino
        net = round(realized - fees - entry_fee - funding_paid, 8)     # lo mismo en el registro y en el total
        entry_notional = done * D(meta["avg_price"])
        ret = net / entry_notional if entry_notional else D(0)
        slip = None
        if theoretical:                                                # + = peor que el precio teórico
            slip = float((D(theoretical) - avg) / D(theoretical) * 10_000 * side)
        left = abs(self.pf.qty(sym))
        self.trades.append({
            "tipo": "salida", "motivo": reason, "symbol": sym, "lado": meta["side"], "qty": str(done),
            "price": str(avg), "fee": str(fees), "pnl_neto": str(net), "retorno_neto": str(round(ret, 6)),
            "entry_price": meta["avg_price"], "notional_entrada": str(round(entry_notional, 8)),
            "deslizamiento_bps": None if slip is None else round(slip, 2), "entry_ts": meta["entry_ts"],
            "ts": round(ts or self.now, 3), "event_id": meta["event_id"], "category": meta["category"],
            "strategy_tag": meta.get("strategy_tag", STRATEGY_TAG), "mercado": self.market.name, "modo": "paper"})
        self.counts[reason] += 1
        if left > 0:                                                   # cierre parcial (tope de precio): sigue abierta
            meta["qty"] = str(left * side)
            meta["entry_fee"] = str(D(meta["entry_fee"]) - entry_fee)
            meta["funding_paid"] = str(D(meta.get("funding_paid", "0")) - funding_paid)
        else:
            self.st["positions"].pop(sym)
            stats = self.st["stats"]
            stats["operaciones"] += 1
            stats["ganadas" if net > 0 else "perdidas"] += 1
            stats["pnl_neto"] = str(D(stats["pnl_neto"]) + net)
            self.risk.on_close(float(net), self.now, self.limits)
        await self._alert(exit_alert(sym, side, reason, done, avg, pnl=round(net, 4), ret=ret,
                                     slippage_bps=slip, why=why, mode=self.market.label,
                                     level=theoretical if reason in ("stop_loss", "take_profit") else None))

    async def exchange_exits(self) -> None:
        """Lo que el exchange habría ejecutado entre corridas: stop, take-profit y expiración, vela por vela.
        La vela del minuto en curso también cuenta: lo que ya pasó en ella pasó (la próxima corrida la revisa
        entera desde su apertura)."""
        end = minute(self.now)
        slip = STOP_SLIPPAGE_BPS / 10_000
        for sym, meta in list(self.st["positions"].items()):
            c = self.store.candles.get(sym) or {}
            side = 1 if meta["side"] == "largo" else -1
            start = max(minute(meta["entry_ts"]), minute(self.st["last_check"]))
            trail = float(meta.get("trail") or 0)
            state = {"stop": float(meta["stop"]), "best": float(meta.get("best") or meta["avg_price"])} if trail else None
            hit = bar.walk(c, side=side, stop=float(meta["stop"]), tp=float(meta["tp"]), start_ms=start, end_ms=end,
                           entry_ms=minute(meta["entry_ts"]), until_ms=minute(meta["until"]) + MIN, slip=slip,
                           trail=trail, state=state)
            if state is not None:                                      # el stop móvil queda donde llegó
                meta["stop"], meta["best"] = str(state["stop"]), str(state["best"])
            await self.market.settle(self, sym, meta, hit[2] if hit else int(self.now * 1000))   # funding hasta salir
            if hit is None:
                continue
            kind, px, m = hit
            inst = self.reg.get(sym)
            price = D(str(px))
            reason = {"sl": "stop_loss", "tp": "take_profit", "exp": "expiracion"}[kind]
            if kind == "sl" and trail_in_profit(meta, side):
                reason = "stop_movil"
            liq = float(meta["liq"]) if meta.get("liq") else None
            if liq is not None and kind != "tp" and (px <= liq if side > 0 else px >= liq):
                price, reason = D(meta["liq"]), "liquidacion"           # un salto pasó la liquidación: se pierde el margen
            if inst is not None and kind != "tp":                      # stop y expiración: al tick, del lado malo
                price = inst.round_price(price, up=side < 0)
            theoretical = D(meta["stop"]) if kind == "sl" else D(str(c[m][0])) if kind == "exp" else D(meta["tp"])
            await self.close(sym, reason, price=price, liquidity="maker" if kind == "tp" else "taker",
                             ts=m / 1000 + (0 if kind == "exp" else 60), theoretical=theoretical)

    async def news_exits(self, items) -> None:
        """Una noticia relevante (C ≥ 7) en contra de una posición abierta: el agente sale a mercado."""
        for ev, sig, news_why in items:
            if news_why or not sig.polarity:
                continue
            for asset in sig.assets:
                sym = self.market.symbol(asset)
                meta = self.st["positions"].get(sym)
                if meta is None or (sig.polarity > 0) == (meta["side"] == "largo"):
                    continue
                a = assess(ev, sig, asset, self.now, self.cfg, self.meta)
                if a.vector.C < self.cfg.c_min:
                    continue
                await self.close(sym, "noticia_en_contra",
                                 why=f"{sig.category} {sig.polarity:+.2f}: {ev['title'][:140]}")

    async def flatten(self, why: str, reason: str = "freno") -> None:
        for sym in list(self.st["positions"]):
            await self.close(sym, reason, why=why)

    # -- entradas -----------------------------------------------------------------------------------
    def record(self, ev, sig, sym, decision: str, reasons=(), **extra) -> None:
        self.decisions.append({"event_id": ev["id"], "symbol": sym, "signal": sig.as_dict(), "ts": round(self.now),
                               "title": ev["title"][:200], "source": ev["source"], "decision": decision,
                               "motivos": list(reasons), "strategy_tag": STRATEGY_TAG, "mercado": self.market.name,
                               **extra})
        if decision != "entrar":
            self.counts["descartada"] += 1

    def sample_spread(self, sym: str, book) -> float | None:
        """z-score del spread actual contra su historia (antes de sumarlo) y lo agrega a la historia."""
        if book.spread_bps is None:
            return None
        bps = float(book.spread_bps)
        st = self.spreads.setdefault(sym, SpreadStats())
        z = st.z(bps)
        st.update(bps, self.now)
        return z

    async def enter(self, ev: dict, sig, asset: str, blocks: list) -> None:
        sym = self.market.symbol(asset)
        a = assess(ev, sig, asset, self.now, self.cfg, self.meta, allows_short=self.market.allows_short)
        reasons = list(a.reasons) + [why for _, why in blocks]           # primero lo del evento, después el resto
        if sym in self.st["positions"]:
            reasons.append("ya_en_cartera")
        inst = self.reg.get(sym)
        if inst is None and not reasons:              # sin datos del contrato solo importa si podía entrar
            reasons.append("par_sin_filtros")
        if reasons:
            self.record(ev, sig, sym, "no_entrar", reasons, analisis=a.as_dict())
            return
        candles = self.store.candles.get(sym) or {}
        t0 = ev.get("published_at") if a.dt_basis == "publicada" else None
        sigma = bar.sigma_1m(candles, minute(t0 if t0 is not None else self.now))
        try:                                          # libro y precio a la vez: cada pedido a MEXC tarda
            book, tick = await asyncio.gather(self.books.book(sym), self.market.ticker(self.session, sym))
        except MarketDataError as e:
            self.record(ev, sig, sym, "no_entrar", [f"sin_datos:{str(e)[:80]}"], analisis=a.as_dict())
            return
        z = self.sample_spread(sym, book)
        gross, net = self.exposure()
        spread = float(book.spread_bps or 0) / 10_000
        price = float(self.mark(sym)) or tick["last"]
        cost_fn = getattr(self.market, "funding_cost", None)          # futuros: el funding en contra es un costo
        funding = cost_fn(sym, tick.get("funding_rate"), a.side, self.now, sig.horizon_min * 60) if cost_fn else 0.0
        p, why = plan(a, sig, sym, equity=float(self.equity()), cash=float(self.pf.cash), gross=gross, net=net,
                      n_positions=len(self.st["positions"]), price=price, sigma_1m=sigma, adv=tick["adv"],
                      fee_taker=float(inst.taker_fee_bps) / 10_000, spread=spread, kind=self.market.kind,
                      max_leverage=float(inst.max_leverage),
                      min_notional=max(float(inst.min_notional), float(inst.min_qty) * price * 1.01),   # un lote entero
                      limits=self.limits, cfg=self.cfg, funding=funding)
        analysis = {**a.as_dict(), "spread_z": None if z is None else round(z, 2), "ultimo": tick["last"],
                    "adv_usdt": round(tick["adv"]), "funding_en_contra": round(funding, 6)}
        if tick.get("fair"):
            analysis["marca"] = tick["fair"]
        if p is not None:
            analysis["plan"] = p.as_dict()
            moved = news_move(candles, t0, price, p.side)
            analysis["movido_desde_la_noticia"] = None if moved is None else round(moved, 5)
            why = chase_blocks(moved, p.sl_frac, p.tp_frac, sig.category) + market_blocks(
                book, p.notional, self.limits, self.cfg, p.side, spread_z=z, last=tick["last"],
                kyle_ratio=kyle_ratio(candles, minute(self.now)), fair=tick.get("fair") or None)
            if self.market.kind != "spot" and not liquidation_ok(1.0, p.side, p.sl_frac, float(inst.max_leverage),
                                                                 self.market.mmr_for(sym), self.cfg):
                why.append("liquidacion_cerca_del_stop")
        if why:
            self.record(ev, sig, sym, "no_entrar", why, analisis=analysis)
            return
        entry_side = Side.BUY if p.side > 0 else Side.SELL
        ref_px = book.best_ask if p.side > 0 else book.best_bid
        emergency = inst.round_price(ref_px * (1 - D(str(EMERGENCY_STOP_MULT * p.sl_frac)) * p.side), up=p.side < 0)
        order, block = await self.service.execute(OrderIntent(
            client_id=f"pp-{ev['id']}-{sym}", symbol=sym, side=entry_side, type=OrderType.MARKET, notional=p.notional,
            max_slippage_bps=D(str(entry_slippage(self.cfg, sig.category, self.limits))), ref_price=ref_px,
            tags={"decision_id": ev["id"], "role": "entry", "strategy_tag": STRATEGY_TAG,
                  "emergency_stop": str(emergency)}))
        if order is None or not order.filled_qty:
            self.counts["entrada_fallida"] += 1
            self.record(ev, sig, sym, "rechazada", [block or (order.reason if order else "sin_orden")], analisis=analysis)
            return
        avg = order.avg_price
        stop_px, tp_px = bar.prices(float(avg), p.side, p.sl_frac, p.tp_frac)
        stop = inst.round_price(D(str(stop_px)), up=p.side < 0)          # el stop, del lado de más protección
        tp = inst.round_price(D(str(tp_px)), up=p.side > 0)              # el take-profit, del lado más exigente
        if p.trail or not p.target:                                      # sin techo: sale por el stop o el tiempo
            tp = inst.round_price(avg * D(100) if p.side > 0 else avg / D(100), up=p.side > 0)
        notional = order.filled_qty * avg
        self.st["positions"][sym] = {
            "side": SIDE_NAME[p.side], "qty": str(order.filled_qty * p.side), "avg_price": str(avg),
            "entry_ts": round(self.now, 3), "stop": str(stop), "tp": str(tp), "until": round(self.now + p.horizon_s),
            "event_id": ev["id"], "cluster": ev.get("cluster"), "category": sig.category, "p": round(p.p, 4),
            "alpha": round(p.alpha, 4), "entry_fee": str(order.fees), "notional": str(round(notional, 8)),
            "risk_usd": p.risk_usd, "strategy_tag": STRATEGY_TAG, "emergency_stop": str(emergency)}
        if p.trail:
            self.st["positions"][sym].update(trail=p.trail, best=str(avg))
        await self.market.on_entry(self, sym, self.st["positions"][sym])
        self.st["clusters"] = (self.st["clusters"] + [ev.get("cluster")])[-500:]
        self.counts["entradas"] += 1
        self.trades.append({"tipo": "entrada", "symbol": sym, "lado": SIDE_NAME[p.side], "qty": str(order.filled_qty),
                            "price": str(avg), "fee": str(order.fees), "notional": str(round(notional, 8)),
                            "ts": round(self.now, 3), "event_id": ev["id"], "category": sig.category,
                            "stop": str(stop), "tp": str(tp), "plan": p.as_dict(), "order_id": order.order_id,
                            "strategy_tag": STRATEGY_TAG, "mercado": self.market.name, "modo": "paper"})
        self.record(ev, sig, sym, "entrar", analisis=analysis)
        await self._alert(entry_alert(sym, p.side, order.filled_qty, avg, notional, stop=stop, tp=tp, p=p.p,
                                      alpha=p.alpha, category=sig.category, title=ev["title"], source=ev["source"],
                                      order_id=order.order_id, mode=self.market.label,
                                      expiry_min=p.horizon_s // 60, trail=p.trail, target=p.target))


def entry_slippage(cfg: PolicyConfig, category: str, limits: dict) -> float:
    """Tope de precio de la entrada: el de la categoría (en un listado el precio se escapa rápido), nunca más que
    el del sobre (`per_trade.max_slippage_bps`)."""
    prof = cfg.exits.get(category)
    want = prof.max_slippage_bps if prof is not None else cfg.max_slippage_bps
    return min(float(want), float(limits["per_trade"]["max_slippage_bps"]))


def trail_in_profit(meta: dict, side: int) -> bool:
    """¿El stop móvil ya está del lado de la ganancia? Entonces salir por él es cobrar, no cortar una pérdida."""
    if not meta.get("trail"):
        return False
    stop, entry = D(meta["stop"]), D(meta["avg_price"])
    return stop > entry if side > 0 else stop < entry


def _save(base: Path, now: float, st: dict, trades: list[dict], decisions: list[dict]) -> None:
    """Todo al final de la corrida (en un hilo): operaciones, decisiones y el estado."""
    _append(base / TRADES / f"{_month(now)}.jsonl", trades)
    _append(base / DECISIONS / f"{_month(now)}.jsonl", decisions)
    paths.write_json_atomic(base / STATE, st)


MARKET_ES = {"binance-spot": "Binance", "mexc-futuros": "futuros MEXC", "binance-futuros": "futuros Binance"}


def day_stats(base: Path, day: str) -> dict:
    """Operaciones cerradas ese día (UTC) en una cuenta: cuántas, cuántas ganadas y el resultado neto."""
    st = paths.read_json(Path(base) / STATE, None)
    if not st:
        return {}
    closed = [r for r in _all_trades(Path(base))
              if r.get("tipo") == "salida" and time.strftime("%Y-%m-%d", time.gmtime(r["ts"])) == day]
    return {"st": st, "closed": len(closed), "won": sum(D(r["pnl_neto"]) > 0 for r in closed),
            "pnl": sum((D(r["pnl_neto"]) for r in closed), D(0))}


def paper_line(data_dir, day: str, market_name: str = BinanceSpot.name) -> str:
    """Línea del resumen diario de la práctica de respaldo (GitHub), en castellano simple."""
    s = day_stats(Path(data_dir) / DIR / market_name, day)
    if not s:
        return ""
    st, n, won = s["st"], s["closed"], s["won"]
    ops = (f"{ops_es(n)} ({won} ganada{'s' if won != 1 else ''}), {usdt_es(s['pnl'])}"
           if n else "sin operaciones")
    open_ = ", ".join(esc(asset_es(x)) for x in sorted(st["positions"]))
    halted = " ⛔ Frenada por seguridad: hay que rearmarla." if (st.get("risk") or {}).get("halted") else ""
    return (f"🧪 Práctica de respaldo en {MARKET_ES.get(market_name, esc(market_name))}: {ops}. Cuenta: "
            f"{usdt_es(D(st.get('equity', st['cash'])), False)} (empezó con {usdt_es(D(st['initial']), False)})."
            + (f" Abiertas: {open_}." if open_ else "") + halted)


async def run_paper(data_dir, items: list[tuple[dict, object]], *, clock, session, limits: dict, telegram=None,
                    capital: float = 120.0, market=None, urls: dict | None = None, candle_urls: dict | None = None,
                    cfg: PolicyConfig | None = None, rearm: bool = False, disabled: tuple = (),
                    learned: dict | None = None, paper_config: PaperConfig | None = None) -> dict:
    """Una corrida del operador paper en un mercado. `items` = [(evento, señal)] de las noticias nuevas.
    `disabled` = {"categoría:lado"} que el aprendizaje apagó; `learned` = su resultado (meta-modelo)."""
    market = market or BinanceSpot(urls, candle_urls)
    cfg = cfg or PolicyConfig()
    data = Path(data_dir)
    base = data / DIR / market.name
    now = clock.time()
    st = await asyncio.to_thread(paths.read_json, base / STATE, None)
    first = st is None
    st = st or new_state(capital, now, market)
    killed = await asyncio.to_thread((data / "KILL").exists)
    trades_hist = await asyncio.to_thread(_all_trades, base)
    pf = _portfolio(st, market)
    meta_model = MetaModel((learned or {}).get("meta"))
    clusters = set(st["clusters"])
    evaluated = [(ev, sig, news_blocks(ev, sig, now, limits, clusters)) for ev, sig in items]
    candidates = []
    for ev, sig, news_why in evaluated:
        for asset in sig.assets:
            side = "largo" if sig.polarity > 0 else "corto"
            off = [f"categoria_apagada_por_evidencia:{sig.category}"] if f"{sig.category}:{side}" in disabled else []
            candidates.append((ev, sig, asset, news_why + off))
    wanted = {market.symbol(c[2]) for c in candidates if not c[3]}
    symbols = sorted(set(st["positions"]) | wanted | {market.symbol(a) for a in CORE})
    registry = await market.instruments(session, symbols, st["instruments"], now)
    store = market.candle_store(session)
    since = max(min(minute(st["last_check"]), minute(now) - 130 * MIN), minute(now) - MAX_GAP_MIN * MIN)
    await store.load({s: [(since, minute(now))] for s in sorted(set(st["positions"]) | wanted)})
    books = market.books(session, clock)
    venue = PaperVenue(registry, books, pf, clock, paper_config or PaperConfig(
        max_book_age_s=float(limits["market"]["max_book_age_s"]), stop_slippage_bps=D(STOP_SLIPPAGE_BPS)))
    service = ExecutionService(venue, EventBus(), clock, kill_file=data / "KILL", mode="PAPER", limits=limits)
    run = _Run(market, st, pf, registry, service, books, store, clock, limits, telegram, now, cfg, meta_model, session)
    if first:
        await run._alert(system_alert(
            f"🧪 Empecé una cuenta de práctica en {MARKET_ES.get(market.name, market.name)} con {capital:.0f} USDT "
            "simulados (precios reales, plata no). Te aviso cada operación y cada freno.", silent=False))
    if rearm:
        run.risk.rearm()
    await run.exchange_exits()
    await run.news_exits(evaluated)
    equity = run.equity()
    run.risk.roll(now, float(equity))
    blocks = run.risk.blocks(now, float(equity), limits)
    fresh = run.risk.new_alerts(blocks)
    for why in fresh:
        await run._alert(circuit_breaker_alert(why))
    if any(k == "halt" or k.startswith("dia:") for k, w in blocks if w in fresh):
        await run.flatten("freno de riesgo: cierre total (informe)")
    version = evaluation.by_version(trades_hist + run.trades).get(STRATEGY_TAG)
    if version and version["veredicto"] == "descartar":
        blocks.append(("dsr", f"estrategia_descartada:dsr={version['dsr']:.2f}<{evaluation.DISCARD}"))
        if st.get("aviso_descarte") != STRATEGY_TAG:
            st["aviso_descarte"] = STRATEGY_TAG
            await run._alert(system_alert(f"📉 La estrategia actual viene dando mal resultado en la práctica "
                                          f"({version['n']} operaciones): dejo de abrir operaciones con ella hasta "
                                          "que haya una versión mejorada.", silent=False))
    if killed:
        blocks = [("killed", "interruptor_KILL"), *blocks]
    for asset in CORE:                                                  # historia del spread (z-score)
        sym = market.symbol(asset)
        if sym not in wanted and registry.get(sym) is not None:
            try:
                run.sample_spread(sym, await books.book(sym))
            except MarketDataError:
                pass
    seen = set()
    for ev, sig, asset, news_why in sorted(candidates, key=lambda c: -abs(c[1].polarity * c[1].confidence)):
        if (ev["id"], asset) in seen:
            continue
        seen.add((ev["id"], asset))
        if news_why:
            run.record(ev, sig, market.symbol(asset), "no_entrar", news_why)
            continue
        await run.enter(ev, sig, asset, blocks)
    equity = run.equity()
    st.update(cash=str(pf.cash), fees=str(pf.fees_total), realized=str(pf.realized_total), risk=run.risk.as_dict(),
              last_check=now, equity=str(round(equity, 6)), updated_at=round(now),
              spreads={s: v.as_dict() for s, v in run.spreads.items()})
    for sym, meta in st["positions"].items():
        if sym in pf.positions:
            meta["avg_price"] = str(pf.positions[sym].avg_price)
    await asyncio.to_thread(_save, base, now, st, run.trades, run.decisions)
    day_pnl = float(equity) - run.risk.day_start if run.risk.day_start else 0.0
    return {"mercado": market.name, "equity": round(float(equity), 4), "cash": round(float(pf.cash), 4),
            "abiertas": sorted(st["positions"]), "pnl_dia": round(day_pnl, 4), "frenos": [why for _, why in blocks],
            **dict(run.counts), "operaciones": st["stats"]["operaciones"], "pnl_neto_total": st["stats"]["pnl_neto"]}
