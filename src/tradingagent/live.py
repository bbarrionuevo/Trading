"""Operador paper EN VIVO (la PC del dueño, 24/7): el mismo núcleo de decisión que el operador por corridas,
pero reaccionando a cada noticia en el momento en que llega y vigilando las barreras cada segundo.

- Noticia aceptada por la ingesta → clasificador determinista (y Claude Haiku si duda, con tope de
  tiempo) → `news_blocks` → `_Run.enter` (vector y alfa, meta-modelo, triple barrera, costos, ¼-Kelly,
  libro real, ExecutionService → PaperVenue). El alfa decae con vida media de 120 s: acá Δt son segundos
  (en GitHub Actions eran minutos).
- Barreras con el libro real cada segundo: stop-loss, take-profit (cuando el libro lo cruza) y
  expiración; salida también por una noticia relevante en contra. Salir siempre se puede (KILL incluido).
- Cada 30 s: muestra de spreads (z-score), frenos de riesgo con el equity marcado (el diario y el halt
  cierran todo), funding de futuros y estado guardado. Un reinicio no borra nada: posiciones, caja,
  frenos y pérdidas del día viven en `TRADING_HOME/paper-vivo/<mercado>/estado.json`.
- Cada 15 min, en segundo plano, el recolector (etiquetas de precio + aprendizaje) en `TRADING_HOME/datos`,
  sin Telegram (eso ya lo hace el de GitHub); el aprendizaje solo reduce riesgo (C-5).
Todo es paper: no existe código de órdenes reales (REGLAS A-2, A-3).
"""
import asyncio
import logging
import time
from pathlib import Path

from . import paths
from .alerts.model import circuit_breaker_alert, system_alert
from .core.bus import EventBus
from .decision.policy import PolicyConfig, news_blocks
from .execution.paper import PaperConfig, PaperVenue
from .execution.service import ExecutionService
from .intelligence import llm as llm_mod
from .intelligence.classifier import classify
from .intelligence.meta import MetaModel
from .marketdata.providers import MarketDataError
from .research import learn, shadow
from .research.labeler import MIN, minute

log = logging.getLogger("tradingagent.live")
WATCH_S, HOUSEKEEP_S, LEARN_EVERY_S = 1.0, 30.0, 900.0


class _LiveRun(shadow._Run):
    """Una "corrida" instantánea: las marcas salen del libro (no de velas cerradas)."""

    def mark(self, sym: str):
        m = self.service.venue.marks.get(sym)
        return m if m is not None else super().mark(sym)


class LiveTrader:
    def __init__(self, market, session, clock, limits: dict, *, alerts=None, journal=None, home: Path | None = None,
                 capital: float = 120.0, llm: "llm_mod.LlmClassifier | None" = None, cfg: PolicyConfig | None = None,
                 paper_config: PaperConfig | None = None, learned_path: Path | None = None):
        self.market, self.session, self.clock, self.limits = market, session, clock, limits
        self.alerts, self.journal, self.llm, self.capital = alerts, journal, llm, capital
        self.cfg = cfg or PolicyConfig()
        home = home or paths.home()
        self.base = home / "paper-vivo" / market.name
        self.kill_file = home / "KILL"
        self.learned_path = learned_path or home / "datos" / "aprendizaje" / "estado.json"
        self.paper_config = paper_config
        self.lock = asyncio.Lock()
        self.counts: dict[str, int] = {}

    # -- arranque y estado ------------------------------------------------------------------------------
    async def start(self) -> None:
        now = self.clock.time()
        st = await asyncio.to_thread(paths.read_json, self.base / shadow.STATE, None)
        self.first = st is None
        self.st = st or shadow.new_state(self.capital, now, self.market)
        self.pf = shadow._portfolio(self.st, self.market)
        symbols = sorted(set(self.st["positions"]) | {self.market.symbol(a) for a in shadow.CORE})
        self.registry = await self.market.instruments(self.session, symbols, self.st["instruments"], now)
        self.books = self.market.books(self.session, self.clock)
        self.venue = PaperVenue(self.registry, self.books, self.pf, self.clock, self.paper_config or PaperConfig(
            max_book_age_s=float(self.limits["market"]["max_book_age_s"]),
            stop_slippage_bps=shadow.D(shadow.STOP_SLIPPAGE_BPS)))
        self.service = ExecutionService(self.venue, EventBus(), self.clock, kill_file=self.kill_file, mode="PAPER",
                                        journal=self.journal, limits=self.limits)
        if self.first:
            await self.send_alert(system_alert(
                f"🧪 Operador paper EN VIVO · {self.market.name} · {self.capital:.0f} USDT simulados. Reacciona a "
                "cada noticia en segundos (alfa con vida media de 120 s) y vigila stop, take-profit y expiración "
                "cada segundo.", silent=False))

    def learned(self) -> dict:
        return paths.read_json(self.learned_path, None) or {}

    def run(self) -> _LiveRun:
        store = self.market.candle_store(self.session)
        return _LiveRun(self.market, self.st, self.pf, self.registry, self.service, self.books, store, self.clock,
                        self.limits, self, self.clock.time(), self.cfg, MetaModel(self.learned().get("meta")),
                        self.session)

    async def send_alert(self, alert) -> None:                    # _Run avisa con esto: nunca bloquea
        if self.alerts is not None:
            self.alerts.submit(alert)

    async def save(self, run: _LiveRun) -> None:
        now = self.clock.time()
        self.st.update(cash=str(self.pf.cash), fees=str(self.pf.fees_total), realized=str(self.pf.realized_total),
                       risk=run.risk.as_dict(), updated_at=round(now), equity=str(round(run.equity(), 6)),
                       spreads={s: v.as_dict() for s, v in run.spreads.items()})
        for sym, meta in self.st["positions"].items():
            if sym in self.pf.positions:
                meta["avg_price"] = str(self.pf.positions[sym].avg_price)
        for k, v in run.counts.items():
            self.counts[k] = self.counts.get(k, 0) + v
        if self.journal is not None:
            for row in run.decisions:
                self.journal.record("decision", row)
            for row in run.trades:
                self.journal.record("paper_trade", row)
        await asyncio.to_thread(shadow._save, self.base, now, self.st, run.trades, run.decisions)
        run.trades, run.decisions = [], []
        run.counts.clear()

    # -- noticias ---------------------------------------------------------------------------------------
    def _event(self, ing) -> dict:
        it = ing.item
        return {"id": it.id, "title": it.title, "body": it.body, "snippet": it.body[:280], "source": it.source,
                "tier": it.tier, "cluster": ing.dedup.cluster_id, "cluster_sources": ing.dedup.distinct_sources,
                "published_at": it.published_at, "received_at": it.received_at, "assets_hint": list(it.assets_hint)}

    async def on_news(self, ing) -> None:
        ev = self._event(ing)
        sig = classify(ev["title"], ev["body"], tier=ev["tier"], cluster_sources=ev["cluster_sources"],
                       hint_assets=tuple(ev["assets_hint"]))
        if self.llm is not None and self.llm.enabled and llm_mod.needs_llm(sig, ev["title"]):
            res = await self.llm.classify(ev["title"], ev["body"], sig)
            if res.signal is not None:
                sig = res.signal                                       # la IA propone; el núcleo decide (C-3)
        async with self.lock:
            run = self.run()
            now = run.now
            why = news_blocks(ev, sig, now, self.limits, set(self.st["clusters"]))
            await run.news_exits([(ev, sig, why)])
            learned = self.learned()
            wanted = [a for a in sig.assets if not why]
            if wanted:
                syms = sorted({self.market.symbol(a) for a in wanted})
                self.registry = await self.market.instruments(self.session, syms, self.st["instruments"], now)
                run.reg = self.registry
                self.venue.registry = self.registry
                await run.store.load({s: [(minute(now) - 130 * MIN, minute(now))] for s in syms})
            blocks = self.risk_blocks(run)
            for asset in sig.assets:
                side = "largo" if sig.polarity > 0 else "corto"
                off = ([f"categoria_apagada_por_evidencia:{sig.category}"]
                       if f"{sig.category}:{side}" in learned.get("apagadas", []) else [])
                if why or off:
                    run.record(ev, sig, self.market.symbol(asset), "no_entrar", why + off)
                    continue
                await run.enter(ev, sig, asset, blocks)
            await self.save(run)

    def risk_blocks(self, run: _LiveRun) -> list:
        equity = float(run.equity())
        run.risk.roll(run.now, equity)                                 # día o semana nuevos: se rearman solos
        blocks = run.risk.blocks(run.now, equity, self.limits)
        if self.kill_file.exists():
            blocks = [("killed", "interruptor_KILL"), *blocks]
        return blocks

    # -- vigilancia -------------------------------------------------------------------------------------
    async def watch_once(self) -> None:
        """Barreras contra el libro real: stop, take-profit cruzado y expiración. Sale a mercado con tope."""
        async with self.lock:
            if not self.st["positions"]:
                return
            run = self.run()
            changed = False
            for sym, meta in list(self.st["positions"].items()):
                side = 1 if meta["side"] == "largo" else -1
                try:
                    book = await self.books.book(sym)
                except MarketDataError:
                    continue
                if book.mid is not None:
                    self.venue.marks[sym] = book.mid                       # el equity se marca con el libro
                px = book.best_bid if side > 0 else book.best_ask          # el precio al que se sale
                if px is None:
                    continue
                stop, tp = shadow.D(meta["stop"]), shadow.D(meta["tp"])
                reason = None
                if (px <= stop) if side > 0 else (px >= stop):
                    reason, theo = "stop_loss", stop
                elif (px > tp) if side > 0 else (px < tp):
                    reason, theo = "take_profit", tp
                elif run.now >= meta["until"]:
                    reason, theo = "expiracion", px
                if reason:
                    await run.close(sym, reason, theoretical=theo)
                    changed = True
            if changed:
                await self.after_exits(run)
                await self.save(run)

    async def after_exits(self, run: _LiveRun) -> None:
        equity = float(run.equity())
        run.risk.roll(run.now, equity)
        blocks = run.risk.blocks(run.now, equity, self.limits)
        fresh = run.risk.new_alerts(blocks)
        for why in fresh:
            await self.send_alert(circuit_breaker_alert(why))
        if any(k == "halt" or k.startswith("dia:") for k, w in blocks if w in fresh):
            await run.flatten("freno de riesgo: cierre total (informe)")

    async def housekeeping(self) -> None:
        """Spreads (z-score), frenos con el equity marcado, funding y estado guardado."""
        async with self.lock:
            run = self.run()
            for asset in shadow.CORE:
                sym = self.market.symbol(asset)
                if self.registry.get(sym) is not None:
                    try:
                        run.sample_spread(sym, await self.books.book(sym))
                    except MarketDataError:
                        pass
            for sym, meta in list(self.st["positions"].items()):
                await self.market.settle(run, sym, meta, int(run.now * 1000))
            await self.after_exits(run)
            await self.save(run)

    async def watch(self, stop: asyncio.Event) -> None:
        last_house = 0.0
        while not stop.is_set():
            try:
                await self.watch_once()
                if time.monotonic() - last_house >= HOUSEKEEP_S:
                    last_house = time.monotonic()
                    await self.housekeeping()
            except Exception:                                          # la vigilancia no se cae nunca
                log.exception("falló una vuelta de vigilancia")
            try:
                await asyncio.wait_for(stop.wait(), timeout=WATCH_S)
            except TimeoutError:
                pass

    def snapshot(self) -> dict:
        return {"mercado": self.market.name, "cash": self.st.get("cash"), "equity": self.st.get("equity"),
                "abiertas": sorted(self.st.get("positions", {})), "operaciones": self.st["stats"]["operaciones"],
                "pnl_neto": self.st["stats"]["pnl_neto"], "halt": bool((self.st.get("risk") or {}).get("halted")),
                **self.counts}


async def learn_loop(stop: asyncio.Event, cfg: dict, env, limits: dict, data_dir: Path,
                     every_s: float = LEARN_EVERY_S) -> None:
    """El recolector cada 15 min en la PC (etiquetas + aprendizaje), sin Telegram: los avisos los da GitHub."""
    from .research.collector import collect
    while not stop.is_set():
        try:
            await collect(data_dir, cfg, env, telegram=None, limits=limits)
            await asyncio.to_thread(learn.update, data_dir, time.time())
        except Exception:                                              # el aprendizaje nunca frena al agente
            log.exception("falló la pasada de aprendizaje")
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_s)
        except TimeoutError:
            pass
