"""Harness de paper: reproduce un escenario (libros + órdenes en el tiempo) y mide la ejecución.

Escenario JSONL, una línea por evento, con `t` en segundos desde el inicio:
  {"config": {"latency_ms": 120, "participation": 0.6, "max_book_age_s": 5, "cash": "5000"}}   (opcional)
  {"t": 0,   "book":   {"symbol": "BTCUSDT", "bids": [["64990", "0.4"]], "asks": [["65000", "0.3"]]}}
  {"t": 0.5, "intent": {"client_id": "e1", "symbol": "BTCUSDT", "side": "buy", "type": "market",
                        "notional": "500", "max_slippage_bps": "30", "tags": {"role": "entry"}}}
La orden viaja con la latencia del modelo sobre el reloj simulado y se llena con el libro vigente al
llegar: el escenario puede mostrar cuánto cuesta llegar tarde a una noticia.
"""
import json
from decimal import Decimal as D
from pathlib import Path

from ..core.clock import ManualClock
from ..marketdata.book import OrderBook
from ..marketdata.providers import ReplayBookProvider
from .instruments import InstrumentRegistry
from .orders import OrderIntent, OrderType, Side, TimeInForce
from .paper import LatencyModel, PaperConfig, PaperVenue
from .portfolio import Portfolio

START = 1_767_225_600.0


def _q(x, dp: int = 8) -> str | None:
    """Decimal legible para el informe (los cálculos internos conservan toda la precisión)."""
    return None if x is None else format(D(x).quantize(D(1).scaleb(-dp)).normalize(), "f")


def _intent(d: dict) -> OrderIntent:
    def dec(k):
        return D(str(d[k])) if d.get(k) is not None else None

    return OrderIntent(client_id=str(d["client_id"]), symbol=d["symbol"], side=Side(d["side"]),
                       type=OrderType(d.get("type", "market")), qty=dec("qty"), notional=dec("notional"),
                       limit_price=dec("limit_price"), stop_price=dec("stop_price"),
                       tif=TimeInForce(d.get("tif", "ioc")), max_slippage_bps=dec("max_slippage_bps"),
                       reduce_only=bool(d.get("reduce_only", False)), ref_price=dec("ref_price"),
                       oco_group=d.get("oco_group"), tags=dict(d.get("tags") or {}))


def load(path) -> tuple[dict, list[dict]]:
    cfg, rows = {}, []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
        except ValueError as e:
            raise ValueError(f"{path}:{n}: JSON inválido ({e})") from e
        if "config" in d:
            cfg.update(d["config"])
        else:
            rows.append(d)
    return cfg, sorted(rows, key=lambda r: float(r.get("t", 0)))


async def run_scenario(path, cash: D = D("10000")) -> dict:
    cfg, rows = load(path)
    clock = ManualClock(START)
    books = [OrderBook.from_levels(r["book"].get("venue", "sim"), r["book"]["symbol"], r["book"]["bids"],
                                   r["book"]["asks"], START + float(r["t"])) for r in rows if "book" in r]
    provider = ReplayBookProvider(books, clock)

    async def sleep(s):
        clock.advance(s)

    venue = PaperVenue(InstrumentRegistry(), provider, Portfolio(D(str(cfg.get("cash", cash)))), clock,
                       PaperConfig(latency=LatencyModel(median_ms=float(cfg.get("latency_ms", 80)),
                                                        sigma=float(cfg.get("latency_sigma", 0.6))),
                                   participation=D(str(cfg.get("participation", "0.8"))),
                                   max_book_age_s=float(cfg.get("max_book_age_s", 5))),
                       seed=int(cfg.get("seed", 0)), sleep=sleep)
    events = []
    venue.on_event = lambda ev: events.append({"t": round(ev.ts - START, 3), "event": ev.kind,
                                               "client_id": ev.order["client_id"], "status": ev.order["status"]})
    for r in rows:
        target = START + float(r.get("t", 0))
        if target > clock.time():
            clock.set(target)
        if "book" in r:
            await venue.on_book(await provider.book(r["book"]["symbol"]))
        elif "intent" in r:
            await venue.submit(_intent(r["intent"]))
    orders = []
    for o in venue.orders.values():
        slips = [f.slippage_bps for f in o.fills if f.slippage_bps is not None]
        orders.append({"client_id": o.intent.client_id, "symbol": o.intent.symbol, "side": str(o.intent.side),
                       "status": str(o.status), "qty": str(o.qty), "filled": str(o.filled_qty),
                       "avg_price": _q(o.avg_price), "fees": _q(o.fees),
                       "slippage_bps": str(round(sum(slips) / len(slips), 2)) if slips else None,
                       "reason": o.reason})
    return {"orders": orders, "events": events, "summary": venue.summary()}
