"""Operador paper "como si fuera real" en Binance spot, por corridas cortas (el recolector, cada 5 min).

En cada corrida, en este orden:
1. **Salidas que el exchange haría solo:** stop-loss y take-profit en reposo (OCO), simulados con las
   velas de 1 minuto desde la corrida anterior. Si una vela toca los dos, cuenta el stop; el
   take-profit se llena solo si el precio lo atraviesa; el stop se llena con 20 bps de deslizamiento,
   o desde la apertura si el precio saltó por debajo.
2. **Salidas del agente:** cumplido el horizonte de la noticia, o ante una noticia negativa sobre un
   activo en cartera, vende a mercado con tope contra el libro real del momento.
3. **Frenos** (REGLAS A-4), con el equity marcado a mercado. Salir siempre se puede.
4. **Entradas:** cada noticia nueva que pasa la política (decision/policy.py) se compra a mercado con
   tope, contra el libro real.

Datos públicos de Binance (data-api.binance.vision): profundidad, filtros del par (tick, lote, mínimo)
y comisión estándar de 0,10 %. Toda orden pasa por ExecutionService (la única puerta, D-0) y el
PaperVenue (latencia, profundidad, tope).

Archivos:
- `paper/estado.json`: estado.
- `paper/operaciones/AAAA-MM.jsonl`: cada operación (F-2: "paper").
- `paper/decisiones/AAAA-MM.jsonl`: cada decisión con su motivo (F-1).

Un archivo `KILL` en la carpeta de datos (la rama `data`) frena las entradas.
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
from ..alerts.model import circuit_breaker_alert, esc, risk_alert, system_alert, trade_alert
from ..core.bus import EventBus
from ..decision.policy import PolicyConfig, atr_5m, market_blocks, news_blocks, plan_entry
from ..decision.risk import RiskState
from ..execution.instruments import Instrument, InstrumentRegistry
from ..execution.orders import OrderIntent, OrderType, Side
from ..execution.paper import PaperConfig, PaperVenue
from ..execution.portfolio import Portfolio, Position
from ..execution.service import ExecutionService
from ..intelligence.classifier import VERSION as NLP_VERSION
from ..marketdata.providers import MarketDataError, RestBookProvider
from .labeler import MIN, CandleStore, minute

log = logging.getLogger("tradingagent.paper")
DIR, STATE, TRADES, DECISIONS = "paper", "estado.json", "operaciones", "decisiones"
STRATEGY_TAG = f"{NLP_VERSION}+riesgo-1"
DEPTH_URL = "https://data-api.binance.vision/api/v3/depth"
INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"
STOP_SLIPPAGE_BPS = 20
EXIT_SLIPPAGE_BPS = 50
FEE_BPS = D("10")                                   # Binance spot estándar: 0,10 % maker y taker
INFO_TTL_S = 86_400
MAX_GAP_MIN = 48 * 60                               # velas hacia atrás como máximo si las corridas se cortaron


def _month(ts: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts))


def _append(path: Path, rows: list[dict]) -> None:
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _save(data: Path, now: float, st: dict, trades: list[dict], decisions: list[dict]) -> None:
    """Todo al final de la corrida (en un hilo): operaciones, decisiones y el estado."""
    _append(data / DIR / TRADES / f"{_month(now)}.jsonl", trades)
    _append(data / DIR / DECISIONS / f"{_month(now)}.jsonl", decisions)
    paths.write_json_atomic(data / DIR / STATE, st)


def parse_symbol_info(s: dict) -> dict:
    """Filtros de un par de exchangeInfo: tick, lote, cantidad mínima y nominal mínimo."""
    f = {x["filterType"]: x for x in s.get("filters", [])}
    notional = f.get("NOTIONAL", {}).get("minNotional") or f.get("MIN_NOTIONAL", {}).get("minNotional") or "5"
    return {"base": s["baseAsset"], "quote": s["quoteAsset"], "tick": f["PRICE_FILTER"]["tickSize"],
            "step": f["LOT_SIZE"]["stepSize"], "min_qty": f["LOT_SIZE"]["minQty"], "min_notional": notional,
            "trading": s.get("status") == "TRADING"}


async def load_instruments(session, symbols, cache: dict, now: float, url: str = INFO_URL) -> InstrumentRegistry:
    for sym in symbols:
        if sym in cache and now - cache[sym].get("ts", 0) < INFO_TTL_S:
            continue
        try:
            async with session.get(url, params={"symbol": sym}, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    raise MarketDataError(f"exchangeInfo {sym}: HTTP {r.status}")
                data = await r.json(content_type=None)
            cache[sym] = {**parse_symbol_info(data["symbols"][0]), "ts": round(now)}
        except Exception as e:                                         # noqa: BLE001 (sin filtros, ese par no se opera)
            log.warning("sin filtros de %s: %s", sym, type(e).__name__)
    reg = InstrumentRegistry(())
    for sym, f in cache.items():
        if f.get("trading", True):
            reg.add(Instrument(sym, "binance", f["base"], f["quote"], tick_size=D(f["tick"]).normalize(),
                               step_size=D(f["step"]).normalize(), min_qty=D(f["min_qty"]),
                               min_notional=D(f["min_notional"]), maker_fee_bps=FEE_BPS, taker_fee_bps=FEE_BPS))
    return reg


def _portfolio(st: dict) -> Portfolio:
    pf = Portfolio(cash=D(st["initial"]))
    pf.cash, pf.fees_total, pf.realized_total = D(st["cash"]), D(st.get("fees", "0")), D(st.get("realized", "0"))
    for sym, p in st["positions"].items():
        pf.positions[sym] = Position(sym, "spot", qty=D(p["qty"]), avg_price=D(p["avg_price"]))
    return pf


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


def paper_line(data_dir, day: str) -> str:
    """Línea del resumen diario: operaciones cerradas ese día (UTC), su PnL neto y cómo quedó la cuenta."""
    data = Path(data_dir)
    st = paths.read_json(data / DIR / STATE, None)
    if not st:
        return ""
    closed = [r for r in _read_rows(data / DIR / TRADES / f"{day[:7]}.jsonl")
              if r.get("tipo") == "salida" and time.strftime("%Y-%m-%d", time.gmtime(r["ts"])) == day]
    won = sum(D(r["pnl_neto"]) > 0 for r in closed)
    pnl = sum((D(r["pnl_neto"]) for r in closed), D(0))
    open_ = ", ".join(esc(s) for s in sorted(st["positions"])) or "ninguna"
    halted = " · ⛔ HALT (rearme con --rearm)" if (st.get("risk") or {}).get("halted") else ""
    return (f"🧪 Paper: {len(closed)} operaciones cerradas ({won} ganadas) · PnL del día {pnl:+.2f} USDT · "
            f"equity {D(st.get('equity', st['cash'])):.2f} de {D(st['initial']):.0f} USDT · abiertas: {open_}{halted}")


def new_state(capital: float, now: float) -> dict:
    return {"initial": str(capital), "cash": str(capital), "positions": {}, "risk": {}, "clusters": [],
            "instruments": {}, "last_check": now, "created_at": round(now), "strategy_tag": STRATEGY_TAG,
            "stats": {"operaciones": 0, "ganadas": 0, "perdidas": 0, "pnl_neto": "0"}}


class _Run:
    """Una corrida del operador: comparte libros, velas, cartera, riesgo y registros."""

    def __init__(self, st, pf, registry, service, books, store, clock, limits, tg, now, cfg):
        self.st, self.pf, self.reg, self.service, self.books, self.store = st, pf, registry, service, books, store
        self.clock, self.limits, self.tg, self.now, self.cfg = clock, limits, tg, now, cfg
        self.trades, self.decisions, self.counts = [], [], Counter()
        self.risk = RiskState.from_dict(st["risk"])

    def mark(self, sym: str) -> D:
        c = self.store.candles.get(sym) or {}
        last = max((m for m in c if m < minute(self.now)), default=None)
        return D(str(c[last][3])) if last is not None else D(self.st["positions"].get(sym, {}).get("avg_price", "0"))

    def equity(self) -> D:
        return self.pf.equity({s: self.mark(s) for s in self.st["positions"]})

    async def _alert(self, alert) -> None:
        if self.tg is not None:
            await self.tg.send_alert(alert)

    async def close(self, sym: str, reason: str, *, price: D | None = None, liquidity: str = "taker", ts=None,
                    why: str = "") -> None:
        """Cierra la posición: con `price` es un llenado del exchange (OCO); sin él, venta a mercado."""
        meta = self.st["positions"][sym]
        inst = self.reg.get(sym)
        qty = self.pf.qty(sym)
        if qty <= 0:                                                   # registro sin cantidad: se limpia
            self.st["positions"].pop(sym)
            return
        if inst is None:                                               # sin filtros del par: no se puede vender,
            self.counts["salida_sin_filtros"] += 1                     # la posición sigue (y su valor también)
            log.warning("no se pudo cerrar %s (%s): el par no tiene filtros del exchange", sym, reason)
            return
        if price is not None:
            fee = inst.fee(qty * price, liquidity)
            realized = self.pf.apply_fill(inst, Side.SELL, qty, price, fee)
            sold, avg, fees = qty, price, fee
        else:
            order, block = await self.service.execute(OrderIntent(
                client_id=f"pp-x-{sym}-{round(self.now)}", symbol=sym, side=Side.SELL, type=OrderType.MARKET,
                qty=qty, max_slippage_bps=D(EXIT_SLIPPAGE_BPS), reduce_only=True,
                tags={"role": "exit", "motivo": reason, "strategy_tag": STRATEGY_TAG}))
            if order is None or not order.filled_qty:
                self.counts["salida_fallida"] += 1
                log.warning("no se pudo vender %s: %s", sym, block or (order.reason if order else ""))
                return
            sold, avg, fees = order.filled_qty, order.avg_price, order.fees
            realized = sum((f.realized_pnl for f in order.fills), D(0))
        entry_fee = D(meta["entry_fee"]) * sold / D(meta["qty"])
        net = round(realized - fees - entry_fee, 8)                    # lo mismo en el registro y en el total
        left = self.pf.qty(sym)
        row = {"tipo": "salida", "motivo": reason, "symbol": sym, "qty": str(sold), "price": str(avg),
               "fee": str(fees), "pnl_neto": str(net), "entry_price": meta["avg_price"],
               "entry_ts": meta["entry_ts"], "ts": round(ts or self.now, 3), "event_id": meta["event_id"],
               "category": meta["category"], "strategy_tag": meta.get("strategy_tag", STRATEGY_TAG), "modo": "paper"}
        self.trades.append(row)
        self.counts[reason] += 1
        if left > 0:                                                   # venta parcial (tope de precio): sigue abierta
            meta["qty"], meta["entry_fee"] = str(left), str(D(meta["entry_fee"]) - entry_fee)
        else:
            self.st["positions"].pop(sym)
            stats = self.st["stats"]
            stats["operaciones"] += 1
            stats["ganadas" if net > 0 else "perdidas"] += 1
            stats["pnl_neto"] = str(D(stats["pnl_neto"]) + net)
            self.risk.on_close(float(net), self.now, self.limits)
        if reason in ("stop_loss", "take_profit"):
            await self._alert(risk_alert(reason, sym, "sell", sold, avg, pnl=round(net, 4)))
        else:
            await self._alert(trade_alert(sym, "sell", sold, avg, why=why or reason, role="exit", pnl=round(net, 4)))

    async def oco(self) -> None:
        """Stops y take-profits en reposo: lo que el exchange habría ejecutado entre corridas.

        Vela por vela desde la anterior corrida. Si una vela toca los dos, cuenta el stop. En el minuto de
        la entrada no se sabe si el mínimo fue antes o después de comprar: ahí solo cuenta el cierre (que sí
        es posterior), para no inventar stops ni take-profits."""
        end = minute(self.now) - MIN                                   # última vela cerrada
        slip = 1 - D(STOP_SLIPPAGE_BPS) / D(10_000)
        for sym, meta in list(self.st["positions"].items()):
            c = self.store.candles.get(sym) or {}
            inst = self.reg.get(sym)
            entry_m = minute(meta["entry_ts"])
            start = max(entry_m, minute(self.st["last_check"]))
            stop, tp = D(meta["stop"]), D(meta["tp"])
            for m in range(start, end + MIN, MIN):
                k = c.get(m)
                if k is None:
                    continue
                o, h, lo, cl = (D(str(x)) for x in k[:4])
                if m == entry_m:
                    o = h = lo = cl                                    # solo el cierre es seguro posterior
                if lo <= stop:
                    px = min(stop, o) * slip                           # salto por debajo: desde la apertura
                    px = inst.round_price(px, up=False) if inst else px
                    await self.close(sym, "stop_loss", price=px, ts=m / 1000 + 60)
                    break
                if h > tp:
                    await self.close(sym, "take_profit", price=tp, liquidity="maker", ts=m / 1000 + 60)
                    break

    async def agent_exits(self, negatives: dict[str, tuple]) -> None:
        for sym, meta in list(self.st["positions"].items()):
            if sym in negatives:
                ev, sig = negatives[sym]
                await self.close(sym, "noticia_negativa", why=f"{sig.category} {sig.polarity:+.2f}: {ev['title'][:140]}")
            elif self.now >= meta["until"]:
                await self.close(sym, "horizonte", why=f"horizonte de {meta['category']} cumplido")

    async def enter(self, ev: dict, sig, sym: str, blocks: list) -> None:
        decision = {"event_id": ev["id"], "symbol": sym, "signal": sig.as_dict(), "ts": round(self.now),
                    "title": ev["title"][:200], "source": ev["source"], "strategy_tag": STRATEGY_TAG}
        reasons = [why for _, why in blocks] or []
        if not reasons and sym in self.st["positions"]:
            reasons = ["ya_en_cartera"]
        inst = self.reg.get(sym)
        if not reasons and inst is None:
            reasons = ["par_sin_filtros"]
        plan = None
        if not reasons:
            candles = self.store.candles.get(sym) or {}
            price = float(self.mark(sym)) if candles else 0.0
            gross = float(self.pf.gross_exposure({s: self.mark(s) for s in self.st["positions"]}))
            plan, reasons = plan_entry(sig, sym, equity=float(self.equity()), cash=float(self.pf.cash), gross=gross,
                                       n_positions=len(self.st["positions"]), price=price,
                                       atr=atr_5m(candles, minute(self.now)), min_notional=float(inst.min_notional),
                                       limits=self.limits, cfg=self.cfg)
        book = None
        if plan is not None:
            try:
                book = await self.books.book(sym)
                reasons = market_blocks(book, plan.notional, self.limits, self.cfg)
            except MarketDataError as e:
                reasons = [f"sin_libro:{str(e)[:80]}"]
        if reasons:
            self.counts["descartada"] += 1
            self.decisions.append({**decision, "decision": "no_entrar", "motivos": reasons})
            return
        order, block = await self.service.execute(OrderIntent(
            client_id=f"pp-{ev['id']}-{sym}", symbol=sym, side=Side.BUY, type=OrderType.MARKET, notional=plan.notional,
            max_slippage_bps=D(str(self.cfg.max_slippage_bps)), ref_price=book.best_ask,
            tags={"decision_id": ev["id"], "role": "entry", "strategy_tag": STRATEGY_TAG}))
        if order is None or not order.filled_qty:
            self.counts["entrada_fallida"] += 1
            self.decisions.append({**decision, "decision": "rechazada",
                                   "motivos": [block or (order.reason if order else "sin_orden")]})
            return
        avg = order.avg_price
        self.st["positions"][sym] = {
            "qty": str(order.filled_qty), "avg_price": str(avg), "entry_ts": round(self.now, 3),
            "stop": str(inst.round_price(avg * (1 - D(str(plan.stop_frac))), up=False)),
            "tp": str(inst.round_price(avg * (1 + D(str(plan.tp_frac))), up=True)),
            "until": round(self.now + plan.horizon_s), "event_id": ev["id"], "cluster": ev.get("cluster"),
            "category": sig.category, "conviction": round(sig.conviction, 3), "entry_fee": str(order.fees),
            "risk_usd": plan.risk_usd, "strategy_tag": STRATEGY_TAG}
        self.st["clusters"] = (self.st["clusters"] + [ev.get("cluster")])[-500:]
        self.counts["entradas"] += 1
        self.trades.append({"tipo": "entrada", "symbol": sym, "qty": str(order.filled_qty), "price": str(avg),
                            "fee": str(order.fees), "notional": str(plan.notional), "ts": round(self.now, 3),
                            "event_id": ev["id"], "category": sig.category, "conviction": round(sig.conviction, 3),
                            "stop": self.st["positions"][sym]["stop"], "tp": self.st["positions"][sym]["tp"],
                            "strategy_tag": STRATEGY_TAG, "modo": "paper"})
        self.decisions.append({**decision, "decision": "entrar", "notional": str(plan.notional)})
        await self._alert(trade_alert(sym, "buy", order.filled_qty, avg, impact=sig.polarity, confidence=sig.confidence,
                                      why=f"{sig.category}: {ev['title'][:160]}", source=ev["source"]))


async def run_paper(data_dir, items: list[tuple[dict, object]], *, clock, session, limits: dict, telegram=None,
                    capital: float = 120.0, urls: dict | None = None, candle_urls: dict | None = None,
                    cfg: PolicyConfig | None = None, rearm: bool = False, disabled: tuple = (),
                    paper_config: PaperConfig | None = None) -> dict:
    """Una corrida del operador paper. `items` = [(evento del recolector, señal)] de las noticias nuevas.
    `disabled` = {"categoría:lado"} que el aprendizaje apagó por evidencia negativa (research/learn.py)."""
    cfg = cfg or PolicyConfig()
    urls = {"depth": DEPTH_URL, "info": INFO_URL, **(urls or {})}
    data = Path(data_dir)
    now = clock.time()
    st = await asyncio.to_thread(paths.read_json, data / DIR / STATE, None)
    first = st is None
    st = st or new_state(capital, now)
    killed = await asyncio.to_thread((data / "KILL").exists)
    pf = _portfolio(st)
    held = list(st["positions"])
    positives, negatives = [], {}
    clusters = set(st["clusters"])
    for ev, sig in items:
        blocks = news_blocks(ev, sig, now, limits, cfg, clusters)
        for asset in sig.assets:
            sym = f"{asset}USDT"
            if sig.polarity < 0 and sym in st["positions"] and not blocks:
                negatives[sym] = (ev, sig)                             # salir siempre se puede
            elif sig.polarity > 0:
                off = [f"categoria_apagada_por_evidencia:{sig.category}"] if f"{sig.category}:largo" in disabled else []
                positives.append((ev, sig, sym, blocks + off))
    symbols = sorted(set(held) | {p[2] for p in positives if not p[3]})
    registry = await load_instruments(session, symbols, st["instruments"], now, urls["info"])
    store = CandleStore(session, urls=candle_urls)
    since = max(min(minute(st["last_check"]), minute(now) - 70 * MIN), minute(now) - MAX_GAP_MIN * MIN)
    await store.load({s: [(since, minute(now))] for s in symbols})
    books = RestBookProvider("binance", session, clock, base_url=urls["depth"], depth=100, max_rps=5)
    venue = PaperVenue(registry, books, pf, clock, paper_config or PaperConfig(
        max_book_age_s=float(limits["market"]["max_book_age_s"]), stop_slippage_bps=D(STOP_SLIPPAGE_BPS)))
    service = ExecutionService(venue, EventBus(), clock, kill_file=data / "KILL", mode="PAPER", limits=limits)
    run = _Run(st, pf, registry, service, books, store, clock, limits, telegram, now, cfg)
    if first:
        await run._alert(system_alert(
            f"🧪 Operador paper activo · {capital:.0f} USDT simulados · libros reales de Binance spot · "
            f"comisiones {FEE_BPS / 100}% · entra con noticias frescas y sale por stop, take-profit, horizonte o "
            "noticia negativa. Avisa cada operación y cada freno.", silent=False))
    if rearm:
        run.risk.rearm()
    await run.oco()
    await run.agent_exits(negatives)
    equity = run.equity()
    run.risk.roll(now, float(equity))
    blocks = run.risk.blocks(now, float(equity), limits)
    for why in run.risk.new_alerts(blocks):
        await run._alert(circuit_breaker_alert(why))
    seen = set()
    for ev, sig, sym, news_why in sorted(positives, key=lambda p: -p[1].conviction):
        if (ev["id"], sym) in seen:
            continue
        seen.add((ev["id"], sym))
        if news_why:
            run.decisions.append({"event_id": ev["id"], "symbol": sym, "signal": sig.as_dict(), "ts": round(now),
                                  "title": ev["title"][:200], "source": ev["source"], "decision": "no_entrar",
                                  "motivos": news_why, "strategy_tag": STRATEGY_TAG})
            run.counts["descartada"] += 1
            continue
        await run.enter(ev, sig, sym, [("killed", "interruptor_KILL")] if killed else blocks)
    equity = run.equity()
    st.update(cash=str(pf.cash), fees=str(pf.fees_total), realized=str(pf.realized_total), risk=run.risk.as_dict(),
              last_check=now, equity=str(round(equity, 6)), updated_at=round(now))
    for sym, meta in st["positions"].items():
        meta["avg_price"] = str(pf.positions[sym].avg_price) if sym in pf.positions else meta["avg_price"]
    await asyncio.to_thread(_save, data, now, st, run.trades, run.decisions)
    day_pnl = float(equity) - run.risk.day_start if run.risk.day_start else 0.0
    return {"equity": round(float(equity), 4), "cash": round(float(pf.cash), 4), "abiertas": sorted(st["positions"]),
            "pnl_dia": round(day_pnl, 4), "frenos": [why for _, why in blocks], **dict(run.counts),
            "operaciones": st["stats"]["operaciones"], "pnl_neto_total": st["stats"]["pnl_neto"]}
