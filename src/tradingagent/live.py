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
from .alerts.model import circuit_breaker_alert, daily_alert, system_alert, usdt_es
from .core.bus import EventBus
from .decision.policy import PolicyConfig, assess, news_blocks
from .decision.risk import RiskState
from .execution.paper import PaperConfig, PaperVenue
from .execution.service import ExecutionService
from .intelligence import llm as llm_mod
from .intelligence import vector as vec
from .intelligence.classifier import classify
from .intelligence.meta import MetaModel
from .marketdata.providers import MarketDataError
from .research import assets as asset_mod
from .research import learn, shadow
from .research.labeler import MIN, minute

log = logging.getLogger("tradingagent.live")
WATCH_S, HOUSEKEEP_S, LEARN_EVERY_S = 1.0, 30.0, 900.0
# La caché del universo dura 6 h. Releerla en cada vuelta (30 s, por cuenta) costaba ~17 ms del bucle (leer 140 KB
# y registrar ~1.000 tickers, medido en la PC el 3-nov) y dejó 1.538 líneas iguales en el log en un día.
UNIVERSE_EVERY_S = 600.0


class _LiveRun(shadow._Run):
    """Una "corrida" instantánea: las marcas salen del libro (no de velas cerradas)."""

    def mark(self, sym: str):
        m = self.service.venue.marks.get(sym)
        return m if m is not None else super().mark(sym)


class LiveTrader:
    def __init__(self, market, session, clock, limits: dict, *, alerts=None, journal=None, home: Path | None = None,
                 capital: float = 120.0, llm: "llm_mod.LlmClassifier | None" = None, cfg: PolicyConfig | None = None,
                 paper_config: PaperConfig | None = None, learned_path: Path | None = None, venue_factory=None,
                 base_dir: Path | None = None, real: bool = False, simulated: bool = False):
        self.market, self.session, self.clock, self.limits = market, session, clock, limits
        self.alerts, self.journal, self.llm, self.capital = alerts, journal, llm, capital
        self.cfg = cfg or PolicyConfig()
        home = home or paths.home()
        self.base = base_dir or home / "paper-vivo" / market.name
        self.venue_factory, self.real = venue_factory, real          # real: el venue de dinero real (A-2)
        self.simulated = simulated                                   # simulacro del real: nunca manda órdenes
        self.kill_file = home / "KILL"
        self.learned_path = learned_path or home / "datos" / "aprendizaje" / "estado.json"
        self.universe_cache = home / "datos" / "universo-mexc.json"
        self.paper_config = paper_config
        self.lock = asyncio.Lock()
        self.counts: dict[str, int] = {}
        self._universe_at, self._universe_n = float("-inf"), None

    # -- arranque y estado ------------------------------------------------------------------------------
    async def start(self) -> None:
        now = self.clock.time()
        st = await asyncio.to_thread(paths.read_json, self.base / shadow.STATE, None)
        self.first = st is None
        self.st = st or shadow.new_state(self.capital, now, self.market)
        self.st["strategy_tag"] = shadow.STRATEGY_TAG     # el tablero muestra la evidencia de la versión que corre
        if self.llm is not None and not self.real:     # el tope diario de la IA sobrevive a reinicios (C-4); lo
            self.llm.budget = llm_mod.Budget.from_dict(self.st.get("llm"))   # lleva la cuenta de práctica
        self.pf = shadow._portfolio(self.st, self.market)
        await self.refresh_universe()
        symbols = sorted(set(self.st["positions"]) | {self.market.symbol(a) for a in shadow.CORE})
        self.registry = await self.market.instruments(self.session, symbols, self.st["instruments"], now)
        self.books = self.market.books(self.session, self.clock)
        if self.venue_factory is not None:
            self.venue = self.venue_factory(self.registry, self.books, self.pf, self.clock)
        else:
            self.venue = PaperVenue(self.registry, self.books, self.pf, self.clock, self.paper_config or PaperConfig(
                max_book_age_s=float(self.limits["market"]["max_book_age_s"]),
                stop_slippage_bps=shadow.D(shadow.STOP_SLIPPAGE_BPS)))
        self.service = ExecutionService(self.venue, EventBus(), self.clock, kill_file=self.kill_file, mode="PAPER",
                                        journal=self.journal, limits=self.limits)
        if self.real and (self.llm is None or not self.llm.enabled):
            await self.send_alert(system_alert(
                "⚠️ Falta la clave de la IA (ANTHROPIC_API_KEY en el .env de la PC): sin la IA no abro operaciones "
                f"con {'el simulacro' if self.simulated else 'dinero real'}. Lo abierto sale normal.", silent=False))
        if self.first and self.real and self.simulated:
            await self.send_alert(system_alert(
                f"🧪 Empecé el SIMULACRO del dinero real en futuros MEXC con {self.capital:g} USDT de mentira: es el "
                "mismo programa que va a operar con tu plata, con los precios reales de MEXC, pero no manda ninguna "
                "orden. Cada operación te llega marcada como SIMULACRO.", silent=False))
        elif self.real:
            await self.sync_capital()                  # todo el saldo: la caja es lo que hay en la cuenta
        elif self.first:
            await self.send_alert(system_alert(
                f"🧪 Empecé la cuenta de práctica en {shadow.MARKET_ES.get(self.market.name, self.market.name)} con "
                f"{self.capital:.0f} USDT simulados (precios reales, plata no). Cuando llegue una noticia que valga "
                "la pena, opero en segundos y te aviso.", silent=False))

    async def refresh_universe(self) -> None:
        """Futuros de MEXC: todo lo operable (criptos, acciones, índices, metales y petróleo), renovado cada 6 h.
        Se mira cada `UNIVERSE_EVERY_S` (no en cada vuelta) y se avisa en el log solo si cambia la cantidad."""
        if not hasattr(self.market, "load_universe"):
            return
        now = self.clock.time()
        if now - self._universe_at < UNIVERSE_EVERY_S:
            return
        self._universe_at = now
        try:
            n = await self.market.load_universe(self.session, self.universe_cache)
            if n != self._universe_n:
                log.info("universo de %s: %d activos operables", self.market.name, n)
                self._universe_n = n
        except Exception:                                              # sin universo, los de siempre
            log.exception("no pude cargar el universo de MEXC")

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
                       spreads={s: v.as_dict() for s, v in run.spreads.items()},
                       marks={s: str(m) for s, m in self.venue.marks.items()})      # para el tablero
        if self.llm is not None:
            self.st["llm"] = self.llm.budget.as_dict()
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

    def worth_asking(self, ev: dict, sig) -> str:
        """'' si vale la pena pagar una consulta a Claude; si no, por qué no. Solo se consulta cuando la
        respuesta puede cambiar lo que se hace: la noticia está fresca, no hay un freno que impida entrar (o hay
        una posición abierta de ese activo que quizá haya que cerrar) y queda lugar para operar."""
        now = self.clock.time()
        pre = [w for w in news_blocks(ev, sig, now, self.limits, set(self.st["clusters"]))
               if not w.startswith("categoria_no_operable")]
        if pre:
            return pre[0].split(":")[0]
        dt, _ = vec.age_s(ev, now)
        if vec.stale(dt, self.cfg.half_life_s):
            return "alfa_obsoleta"
        assets = set(sig.assets) | set(asset_mod.detect(ev["title"]))
        if any(self.market.symbol(a) in self.st["positions"] for a in assets):
            return ""                                                  # una salida a tiempo vale la consulta
        if self.kill_file.exists():
            return "kill"
        equity = float(self.st.get("equity") or self.st["cash"])
        if RiskState.from_dict(self.st.get("risk")).blocks(now, equity, self.limits):
            return "frenos"
        if len(self.st["positions"]) >= int(self.limits["portfolio"]["max_positions"]):
            return "cupo_lleno"
        return ""

    def rules_would_enter(self, ev: dict, sig) -> bool:
        """¿Las reglas solas abrirían algo con esta noticia? Entonces la IA la lee antes de poner plata. Si las
        reglas ya la descartan (poca relevancia, alfa chica), no se paga: la lectura de la IA solo puede achicarla."""
        if not llm_mod.wants_trade(sig):
            return False
        meta, now = MetaModel(self.learned().get("meta")), self.clock.time()
        return any(not assess(ev, sig, a, now, self.cfg, meta, allows_short=self.market.allows_short).reasons
                   for a in sig.assets)

    async def on_news(self, ing) -> None:
        ev = self._event(ing)
        sig = classify(ev["title"], ev["body"], tier=ev["tier"], cluster_sources=ev["cluster_sources"],
                       hint_assets=tuple(ev["assets_hint"]))
        ai_on = self.llm is not None and self.llm.enabled
        rules_in = self.rules_would_enter(ev, sig)                    # ¿las reglas solas abrirían algo?
        if ai_on and (llm_mod.needs_llm(sig, ev["title"]) or rules_in):
            skip = self.worth_asking(ev, sig)
            if skip:                                                   # no cambiaría nada: no se paga
                self.counts["ia_evitada"] = self.counts.get("ia_evitada", 0) + 1
            else:
                res = await self.llm.classify(ev["title"], ev["body"], sig)
                if res.signal is not None:
                    sig = res.signal                                   # la IA propone; el núcleo decide (C-3)
                else:
                    self.counts["ia_sin_respuesta"] = self.counts.get("ia_sin_respuesta", 0) + 1
        # Ninguna entrada sin que la IA haya leído la noticia: si hay IA, siempre; con dinero real, sin IA no se
        # abre nada (las reglas solas leyeron mal 3 de 3 noticias el 3-nov). Salir siempre se puede. Si las reglas
        # igual la descartaban, el motivo que queda registrado es el de ellas (relevancia, alfa...), no la IA.
        confirm = (ai_on or self.real) and rules_in
        async with self.lock:
            run = self.run()
            now = run.now
            why = news_blocks(ev, sig, now, self.limits, set(self.st["clusters"]))
            await run.news_exits([(ev, sig, why)])
            learned = self.learned()
            blocks = self.risk_blocks(run)
            side = "largo" if sig.polarity > 0 else "corto"
            off = ([f"categoria_apagada_por_evidencia:{sig.category}"]
                   if f"{sig.category}:{side}" in learned.get("apagadas", []) else [])
            if confirm and not llm_mod.by_ai(sig):
                off.append("sin_confirmacion_ia")
            # Datos de mercado (contrato y 130 min de velas) solo para lo que puede entrar. Antes se bajaban para toda
            # noticia, con el candado tomado: una noticia que igual se descartaba por relevancia o alfa demoraba hasta
            # 14 s a la siguiente (diario de la PC, 3 y 4-nov), y esa siguiente podía ser un listado.
            ready = [] if (why or off or blocks) else [
                a for a in sig.assets if not assess(ev, sig, a, now, run.cfg, run.meta,
                                                    allows_short=self.market.allows_short).reasons]
            if ready:
                syms = sorted({self.market.symbol(a) for a in ready})
                self.registry = await self.market.instruments(self.session, syms, self.st["instruments"], now)
                run.reg = self.registry
                self.venue.registry = self.registry
                await run.store.load({s: [(minute(now) - 130 * MIN, minute(now))] for s in syms})
            for asset in sig.assets:
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
            changed = moved_any = False
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
                trail = shadow.D(str(meta.get("trail") or 0))
                if trail:                                              # stop que sigue al mejor precio
                    best = shadow.D(meta.get("best") or meta["avg_price"])
                    best = max(best, px) if side > 0 else min(best, px)
                    moved = best * (1 - trail) if side > 0 else best * (1 + trail)
                    inst = self.registry.get(sym)
                    if inst is not None:
                        moved = inst.round_price(moved, up=side < 0)
                    new_stop = max(stop, moved) if side > 0 else min(stop, moved)
                    if new_stop != stop or str(best) != meta.get("best"):
                        meta["best"], meta["stop"], stop, moved_any = str(best), str(new_stop), new_stop, True
                reason = None
                if (px <= stop) if side > 0 else (px >= stop):
                    reason = "stop_movil" if shadow.trail_in_profit(meta, side) else "stop_loss"
                    theo = stop
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
            elif moved_any:                                            # el stop subió: que un reinicio no lo pierda
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

    async def sync_capital(self) -> None:
        """Dinero real SIN tope (el dueño, 3-nov): se opera con todo el saldo de la cuenta de futuros. Sin
        posiciones abiertas, la caja del programa pasa a ser el saldo real. Lo que cambió sin operar (un depósito,
        un retiro, o la diferencia exacta de comisiones y funding) es un movimiento de capital: se registra y los
        frenos se corren igual, para que midan solo lo operado (un retiro no es una pérdida)."""
        if not self.real or self.simulated or self.st["positions"] or getattr(self.venue, "blocked", ""):
            return
        try:
            bal = await self.venue.account(fresh=True)
            equity = shadow.D(str(bal["equity"]))
        except Exception as e:                                         # noqa: BLE001 (sin saldo no se opera)
            log.warning("no pude leer el saldo de la cuenta real: %s", str(e)[:120])
            return
        first = self.st.get("capital_leido") is None
        delta = equity - self.pf.cash
        self.st["capital_leido"] = round(self.clock.time())
        if delta == 0 and not first:
            return
        self.pf.cash = equity
        self.st["cash"] = str(equity)
        # El capital inicial y las referencias de los frenos se corren igual que el saldo: así "ganado" y "perdido"
        # miden solo lo operado (un depósito no es ganancia, un retiro no es pérdida). Sin referencia todavía (por
        # ejemplo, si al arrancar no se pudo leer el saldo), la referencia es el saldo de ahora.
        self.st["initial"] = str(equity if first else shadow.D(self.st["initial"]) + delta)
        risk = self.st.get("risk") or {}
        for k in ("peak", "day_start", "week_start"):
            risk[k] = float(risk[k]) + float(delta) if risk.get(k) else float(equity)
        self.st["risk"] = risk
        self.st["movimientos_capital"] = (self.st.get("movimientos_capital", []) + [
            {"ts": round(self.clock.time()), "usdt": str(round(delta, 8)), "saldo": str(equity)}])[-50:]
        await asyncio.to_thread(paths.write_json_atomic, self.base / shadow.STATE, self.st)
        if first:
            await self.send_alert(system_alert(
                f"💵 Empecé a operar con DINERO REAL en futuros MEXC, con todo el saldo de la cuenta: "
                f"{usdt_es(equity, signed=False)}. Si ganamos, opero con lo que haya. Cada operación te llega marcada "
                "como DINERO REAL. Para frenar todo: python -m tradingagent cerrar-todo", silent=False))
        elif abs(delta) >= 1:
            await self.send_alert(system_alert(
                f"💵 Cambió el saldo de la cuenta de futuros sin operar ({usdt_es(delta)}): ahora opero con "
                f"{usdt_es(equity, signed=False)}.", silent=True))

    async def housekeeping(self) -> None:
        """Saldo real, spreads (z-score), frenos con el equity marcado, funding y estado guardado."""
        await self.refresh_universe()                                  # de la caché salvo cada 6 h
        async with self.lock:
            await self.reconcile()
            await self.sync_capital()
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
            await self.daily_summary(run)
            await self.save(run)

    async def reconcile(self) -> None:
        """Dinero real: si las posiciones de MEXC no coinciden con las del programa, no se abre nada nuevo."""
        if not hasattr(self.venue, "reconcile"):
            return
        before = self.venue.blocked
        now = await self.venue.reconcile()
        if now and now != before and now.startswith("exchange_sin_respuesta"):
            await self.send_alert(system_alert(                         # un corte de red: se reintenta solo
                "⚠️ MEXC no responde (o se cortó internet): no abro nada nuevo hasta que vuelva. Lo abierto tiene "
                "su stop de emergencia en el exchange.", silent=True))
        elif now and now != before:
            what = "del SIMULACRO" if self.simulated else "de DINERO REAL en MEXC"
            await self.send_alert(system_alert(
                f"⚠️ Las posiciones {what} no coinciden con las del programa: no abro nada nuevo hasta que "
                "coincidan. ¿Abriste o cerraste algo a mano en MEXC?", silent=False))
        elif before and not now:
            await self.send_alert(system_alert("✅ Las posiciones de dinero real volvieron a coincidir."))

    async def daily_summary(self, run: _LiveRun) -> None:
        """Una vez por día, al pasar las 00:00 UTC (21:00 en Argentina): cómo le fue ayer a la cuenta."""
        day = time.strftime("%Y-%m-%d", time.gmtime(run.now))
        last = self.st.get("resumen_dia")
        if last == day:
            return
        self.st["resumen_dia"] = day
        if last is None:                                               # primera vez: cuenta desde hoy
            return
        s = await asyncio.to_thread(shadow.day_stats, self.base, last)
        if not s:
            return
        llm = self.llm.budget.as_dict() if self.llm is not None and self.llm.budget.day == last else None
        await self.send_alert(daily_alert(
            last, mode=self.market.label, closed=s["closed"], won=s["won"], pnl=s["pnl"],
            equity=self.st.get("equity") or self.st["cash"], initial=self.st["initial"],
            total_closed=self.st["stats"]["operaciones"], llm=llm))

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

    async def close_all(self, why: str) -> tuple[int, int]:
        """Cierra todo ya (orden del dueño): (posiciones que había, las que quedaron abiertas)."""
        async with self.lock:
            run = self.run()
            before = len(self.st["positions"])
            await run.flatten(why, reason="pedido_del_dueno")
            await self.save(run)
            return before, len(self.st["positions"])

    def status_lines(self, title: str) -> list[str]:
        """Resumen en castellano para /estado (Telegram es privado: va con montos)."""
        st = self.st
        eq, ini = shadow.D(st.get("equity") or st["cash"]), shadow.D(st["initial"])
        out = [f"{title}: {usdt_es(eq, signed=False)} ({usdt_es(eq - ini)} desde el inicio), "
               f"{st['stats']['operaciones']} operaciones cerradas"]
        for sym, p in st["positions"].items():
            out.append(f"  · {p['side']} {sym.replace('_USDT', '')} desde {p['avg_price']}, límite {p['stop']}"
                       + (" (sigue al precio)" if p.get("trail") else ""))
        if (st.get("risk") or {}).get("halted"):
            out.append("  ⛔ frenado por caída: no abre nada hasta revisarlo")
        return out

    def snapshot(self) -> dict:
        return {"mercado": self.market.name, "cash": self.st.get("cash"), "equity": self.st.get("equity"),
                "abiertas": sorted(self.st.get("positions", {})), "operaciones": self.st["stats"]["operaciones"],
                "pnl_neto": self.st["stats"]["pnl_neto"], "halt": bool((self.st.get("risk") or {}).get("halted")),
                "llm": ({**self.llm.budget.as_dict(), "tope_usd": self.llm.limit_usd} if self.llm is not None else None),
                **({"simulacro": self.simulated} if self.real else {}), **self.counts}


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
