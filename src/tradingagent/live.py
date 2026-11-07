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
import json
import logging
import os
import time
from pathlib import Path

from . import paths
from .alerts.model import asset_es, circuit_breaker_alert, daily_alert, system_alert, usdt_es
from .core.bus import EventBus
from .decision import binance_listings, delistings, earnings, launches, upbit_listings
from .decision.binance_listings import coin_key
from .decision.policy import PolicyConfig, assess, news_blocks
from .decision.risk import RiskState
from .execution.live import arming
from .execution.paper import PaperConfig, PaperVenue
from .execution.service import ExecutionService
from .intelligence import llm as llm_mod
from .intelligence import vector as vec
from .intelligence.classifier import classify
from .intelligence.meta import MetaModel
from .marketdata.providers import MarketDataError
from .marketdata.universe import CRYPTO
from .research import assets as asset_mod
from .research import evaluation, learn, shadow
from .research.labeler import MIN, minute

log = logging.getLogger("tradingagent.live")
# Las estrategias con camino propio (no pasan por la lectura general de la noticia): si el armado es solo de estas y no
# hay nada abierto, el operador no lee las demás noticias (el de dinero real solo para Upbit no gasta IA ni candado).
OWN_PATH = frozenset({upbit_listings.CATEGORY, binance_listings.CATEGORY, earnings.CATEGORY, launches.CATEGORY,
                      *delistings.BY_CATEGORY})
WATCH_S, HOUSEKEEP_S, LEARN_EVERY_S = 1.0, 30.0, 900.0
LAUNCH_POLL_S = 60.0                      # catálogo de Binance: contratos nuevos (peso 1 por pedido)
# La caché del universo dura 6 h. Releerla en cada vuelta (30 s, por cuenta) costaba ~17 ms del bucle (leer 140 KB
# y registrar ~1.000 tickers, medido en la PC el 3-nov) y dejó 1.538 líneas iguales en el log en un día.
UNIVERSE_EVERY_S = 600.0


class Stopwatch:
    """Los tiempos de una entrada al instante, por etapa, para el registro (en ms): así se ve en cada aviso real dónde
    se va el tiempo (en los listados de Upbit, entre el segundo 1 y el 5 cada segundo cuenta: REGLAS L-6)."""

    def __init__(self, received_at: float, clock):
        self.t0 = time.perf_counter()
        self.waited_ms = round((clock.time() - received_at) * 1000)      # desde que llegó el aviso hasta empezar
        self.marks: list[tuple[str, int]] = []

    def mark(self, name: str) -> None:
        self.marks.append((name, round((time.perf_counter() - self.t0) * 1000)))

    def text(self) -> str:
        return f"esperó {self.waited_ms} ms · " + " · ".join(f"{n} a los {ms} ms" for n, ms in self.marks)

    def total_ms(self) -> int:
        """Desde que llegó el aviso hasta ahora."""
        return self.waited_ms + round((time.perf_counter() - self.t0) * 1000)


def notice_times(ing) -> dict:
    """El aviso para la mejora continua: cuándo se publicó, cuándo llegó y la demora del canal."""
    pub, rec = ing.item.published_at, ing.item.received_at
    return {"aviso": {"publicado": None if pub is None else round(pub, 3),
                      "recibido": None if rec is None else round(rec, 3),
                      "demora_s": None if pub is None or rec is None else round(rec - pub, 3)}}


def reference(base: float, book, move: float) -> dict:
    """El precio contra el que se midió el freno (la apertura del minuto del aviso o el primero) y el de ese momento."""
    return {"ref": {"precio_base": base, "precio": float(book.mid), "movimiento_pct": round(move * 100, 3)}}


class _LiveRun(shadow._Run):
    """Una "corrida" instantánea: las marcas salen del libro (no de velas cerradas)."""

    def mark(self, sym: str):
        m = self.service.venue.marks.get(sym)
        return m if m is not None else super().mark(sym)


class LiveTrader:
    def __init__(self, market, session, clock, limits: dict, *, alerts=None, journal=None, home: Path | None = None,
                 capital: float = 120.0, llm: "llm_mod.LlmClassifier | None" = None, cfg: PolicyConfig | None = None,
                 paper_config: PaperConfig | None = None, learned_path: Path | None = None, venue_factory=None,
                 base_dir: Path | None = None, real: bool = False, simulated: bool = False,
                 arm_file: str = arming.ARM_FILE, exclude_real: bool = False, owns_llm_budget: bool | None = None):
        self.market, self.session, self.clock, self.limits = market, session, clock, limits
        self.arm_file = arm_file                                     # SIMULACRO.json: el simulacro junto al real
        self.exclude_real = exclude_real                             # nunca opera lo que opera el dinero real
        self.owns_llm_budget = (not real) if owns_llm_budget is None else owns_llm_budget
        self.alerts, self.journal, self.llm, self.capital = alerts, journal, llm, capital
        self.cfg = cfg or PolicyConfig()
        home = home or paths.home()
        self.base = base_dir or home / "paper-vivo" / market.name
        self.home = home
        self.venue_factory, self.real = venue_factory, real          # real: el venue de dinero real (A-2)
        self.simulated = simulated                                   # simulacro del real: nunca manda órdenes
        self.kill_file = home / "KILL"
        self.learned_path = learned_path or home / "datos" / "aprendizaje" / "estado.json"
        self.universe_cache = home / "datos" / getattr(market, "universe_file", "universo-mexc.json")
        self.venue_es = getattr(market, "venue_es", "MEXC")          # en los avisos: Binance o MEXC
        self.paper_config = paper_config
        self.lock = asyncio.Lock()
        self.counts: dict[str, int] = {}
        self._universe_at, self._universe_n = float("-inf"), None
        self._agreement_day = None                                   # aviso del acuerdo TradFi: uno por día
        self.sec_tickers_cache = home / "datos" / "sec-tickers.json"
        self._ciks: dict | None = None                               # CIK de la SEC → tickers (una vez por día)
        self._ciks_at = float("-inf")
        self.pending: set[asyncio.Task] = set()                      # resultados esperando su minuto 2
        self.discarded: frozenset = frozenset()                      # estrategias descartadas por sus resultados
        self._verdict_at = float("-inf")
        self._arms: dict[str, tuple] = {}                            # archivo de armado → (huella, contenido)

    # -- arranque y estado ------------------------------------------------------------------------------
    async def start(self) -> None:
        now = self.clock.time()
        st = await asyncio.to_thread(paths.read_json, self.base / shadow.STATE, None)
        self.first = st is None
        self.st = st or shadow.new_state(self.capital, now, self.market)
        self.st["strategy_tag"] = shadow.STRATEGY_TAG     # el tablero muestra la evidencia de la versión que corre
        if self.llm is not None and self.owns_llm_budget:   # el tope diario de la IA sobrevive a reinicios (C-4); lo
            self.llm.budget = llm_mod.Budget.from_dict(self.st.get("llm"))   # lleva la práctica (o el simulacro)
        self.pf = shadow._portfolio(self.st, self.market)
        warm = getattr(self.llm, "warm_up", None)      # la IA lista antes de la primera noticia (no la frena)
        if warm is not None:
            await warm()
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
                f"🧪 Empecé el SIMULACRO del dinero real en futuros {self.venue_es} con {self.capital:g} USDT de mentira: "
                f"es el mismo programa que va a operar con tu plata, con los precios reales de {self.venue_es}, pero "
                "no manda ninguna "
                "orden. Cada operación te llega marcada como SIMULACRO.", silent=False))
        elif self.real:
            await self.sync_capital()                  # todo el saldo: la caja es lo que hay en la cuenta
        elif self.first:
            await self.send_alert(system_alert(
                f"🧪 Empecé la cuenta de práctica en {shadow.MARKET_ES.get(self.market.name, self.market.name)} con "
                f"{self.capital:.0f} USDT simulados (precios reales, plata no). Cuando llegue una noticia que valga "
                "la pena, opero en segundos y te aviso.", silent=False))

    async def refresh_universe(self) -> None:
        """Futuros: todo lo operable (criptos, acciones, índices, metales y petróleo), renovado cada 6 h.
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
            log.exception("no pude cargar el universo de %s", self.venue_es)

    def learned(self) -> dict:
        return paths.read_json(self.learned_path, None) or {}

    def _arm(self, file: str) -> dict | None:
        """Un archivo de armado (ARMADO.json o SIMULACRO.json), leído solo si cambió."""
        path = self.home / file
        try:
            st = path.stat()
        except OSError:                                                # sin armado el venue no abre nada igual
            self._arms.pop(file, None)
            return None
        mark = (st.st_mtime_ns, st.st_size)
        got = self._arms.get(file)
        if got is None or got[0] != mark:
            data = paths.read_json(path, None)
            got = self._arms[file] = (mark, data if isinstance(data, dict) else None)
        return got[1]

    def armed_only(self) -> frozenset | None:
        """Real o simulacro armado solo para algunas estrategias (`armar-real --estrategias`; el dueño, 6-nov: solo los
        listados de Upbit con dinero real): las demás no abren acá. None: todas (la práctica no tiene armado)."""
        if not self.real:
            return None
        only = (self._arm(self.arm_file) or {}).get("estrategias")
        return frozenset(str(s) for s in only) if isinstance(only, list) else None

    def excluded(self) -> frozenset:
        """El simulacro junto al dinero real nunca opera lo que opera la plata (el dueño, 6-nov: "siempre que los
        simulacros de los demás módulos no compitan o afecten en nada a los módulos que trabajen con dinero real"): las
        estrategias del armado real, leídas en cada noticia (si el dueño suma una, el simulacro la suelta). "*": todas."""
        if not self.exclude_real:
            return frozenset()
        data = self._arm(arming.ARM_FILE)
        if not data or data.get("simulacro"):
            return frozenset()
        only = data.get("estrategias")
        return frozenset(str(s) for s in only) if isinstance(only, list) else frozenset({"*"})

    def allows(self, category: str) -> bool:
        only, out = self.armed_only(), self.excluded()
        return (only is None or category in only) and "*" not in out and category not in out

    def run(self) -> _LiveRun:
        store = self.market.candle_store(self.session)
        run = _LiveRun(self.market, self.st, self.pf, self.registry, self.service, self.books, store, self.clock,
                       self.limits, self, self.clock.time(), self.cfg, MetaModel(self.learned().get("meta")),
                       self.session)
        run.discarded = self.discarded
        run.only = self.armed_only()
        run.excluded = self.excluded()
        return run

    async def refresh_verdicts(self) -> None:
        """Cada estrategia (categoría) con sus propias operaciones cerradas: con 30 o más y DSR < 0,80 deja de abrir
        operaciones (F-3), y se avisa una vez. Corre con la limpieza, como mucho cada 10 minutos."""
        self._verdict_at = self.clock.time()
        trades = await asyncio.to_thread(shadow._all_trades, self.base)
        cats = evaluation.by_version(trades, key="category")
        new = frozenset(c for c, v in cats.items() if v["veredicto"] == "descartar")
        for c in sorted(new - self.discarded):
            await self.send_alert(system_alert(
                f"📉 La estrategia «{c}» viene dando mal resultado ({cats[c]['n']} operaciones, DSR "
                f"{cats[c]['dsr']}): dejo de abrir operaciones con ella. Lo abierto sale normal.", silent=False))
        self.discarded = new

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
        await self._agreement_notice(run.decisions, now)
        await asyncio.to_thread(shadow._save, self.base, now, self.st, run.trades, run.decisions)
        run.trades, run.decisions = [], []
        run.counts.clear()

    async def _agreement_notice(self, decisions: list[dict], now: float) -> None:
        """Binance rechaza los TradFi (acciones, oro, petróleo) hasta que el dueño firma su acuerdo (código -4411,
        visto en la demo el 4-nov). Firmarlo es cosa del dueño: se le avisa una vez por día, no en cada noticia."""
        hits = [r for r in decisions if any("-4411" in str(m) for m in r.get("motivos") or [])]
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if not hits or self._agreement_day == day:
            return
        self._agreement_day = day
        what = "el SIMULACRO (en la demo)" if self.simulated else "el dinero real"
        await self.send_alert(system_alert(
            f"✍️ Binance no deja operar acciones, oro ni petróleo (TradFi) en {what} hasta que aceptes su acuerdo: "
            f"rechazó {asset_es(hits[0].get('symbol', ''))}. Entrá a Binance → Futuros → un contrato TradFi (por ejemplo "
            "TSLAUSDT) y aceptá el acuerdo. Las criptos siguen normal.", silent=False))

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
        # Las reglas al instante de una estrategia que este armado no incluye ni piden datos (no frenan a las que sí).
        cik = earnings.filing_cik(ing.item.source, ing.item.title, ing.item.body)
        if cik is not None and hasattr(self.market, "universe") and self.allows(earnings.CATEGORY):
            task = asyncio.create_task(self._earnings(ing, cik), name=f"resultados-{cik}")   # se mide a los 2 minutos
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)
        kind, delisted = (delistings.notice(ing.item.source, ing.item.title)
                          if getattr(self.market, "name", "") == "binance-futuros" else (None, ()))
        for coin in delisted if kind is not None and self.allows(kind.category) else ():   # deslistado o etiqueta de
            task = asyncio.create_task(self._delisting(ing, coin, kind.category),         # vigilancia: ya, sin la IA
                                       name=f"{kind.category}-{coin}")
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)
        listed = self.upbit_fast(ing)
        for coin in listed if self.allows(upbit_listings.CATEGORY) else ():   # listado de Upbit a tiempo: ya, sin la IA
            task = asyncio.create_task(self._upbit_listing(ing, coin), name=f"listado-upbit-{coin}")
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)
        quick = self.binance_fast(ing)
        for coin in quick if self.allows(binance_listings.CATEGORY) else ():   # listado de Binance a tiempo: ya
            task = asyncio.create_task(self._binance_listing(ing, coin), name=f"listado-binance-{coin}")
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)
        only = self.armed_only()
        if only is not None and only <= OWN_PATH and not self.st["positions"]:
            return               # armado solo para reglas con camino propio y nada abierto: ni IA ni candado (6-nov)
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
            if delisted:                                               # lo opera su regla medida, no la general
                off.append("lo_opera_la_regla_de_deslistados")
            if listed:
                off.append("lo_opera_la_regla_de_listados_de_upbit")
            if quick:
                off.append("lo_opera_la_regla_de_listados_de_binance")
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
                    run.record(ev, sig, self.market.symbol(asset), "no_entrar", why + off, **notice_times(ing))
                    continue
                await run.enter(ev, sig, asset, blocks, extra=notice_times(ing))
            await self.save(run)

    async def _cik_tickers(self) -> dict:
        if self._ciks is None or self.clock.time() - self._ciks_at > 86_400:
            from .marketdata import sec_tickers
            self._ciks = await sec_tickers.load(self.session, self.sec_tickers_cache, os.environ.get("SEC_USER_AGENT"))
            self._ciks_at = self.clock.time()
        return self._ciks or {}

    async def _earnings(self, ing, cik: int) -> None:
        """Resultados con impulso (decision/earnings.py): a los 2 minutos de la aceptación del 8-K, si el perpetuo
        TradFi ya se movió ≥ 0,5 % contra el precio de antes, se entra en esa dirección (stop fijo de 5 %, 30 min)."""
        try:
            tickers = await self._cik_tickers()
            uni = self.market.universe
            tk = next((t for t in tickers.get(cik, ()) if uni.kind(t) == "accion"), None)
            t0 = ing.item.published_at
            if tk is None or t0 is None:
                return
            wait = t0 + earnings.ENTRY_DELAY_S - self.clock.time()
            if wait > 0:
                await asyncio.sleep(wait)
            async with self.lock:
                run = self.run()
                now, ev, sym = run.now, self._event(ing), self.market.symbol(tk)
                late = now - t0
                if late > earnings.MAX_LATE_S:
                    run.record(ev, earnings.signal(tk, 1), sym, "no_entrar", [f"resultados_tarde:{late:.0f}s"],
                               **notice_times(ing))
                    await self.save(run)
                    return
                self.registry = await self.market.instruments(self.session, [sym], self.st["instruments"], now)
                run.reg = self.registry
                self.venue.registry = self.registry
                await run.store.load({sym: [(minute(t0) - 130 * MIN, minute(now))]})
                before = (run.store.candles.get(sym) or {}).get(minute(t0) - MIN)
                try:
                    book = await self.books.book(sym)
                except MarketDataError:
                    book = None
                if not before or book is None or book.mid is None:
                    run.record(ev, earnings.signal(tk, 1), sym, "no_entrar", ["sin_datos:precio_antes_o_libro"],
                               **notice_times(ing))
                    await self.save(run)
                    return
                move = float(book.mid) / float(before[3]) - 1                   # contra el cierre de antes del 8-K
                side = 1 if move > 0 else -1
                sig = earnings.signal(tk, side)
                x = {**notice_times(ing), **reference(float(before[3]), book, move),
                     "params": {"umbral": earnings.THRESHOLD, "max_late_s": earnings.MAX_LATE_S}}
                if abs(move) < earnings.THRESHOLD:
                    run.record(ev, sig, sym, "no_entrar", [f"impulso_chico:{move * 100:+.2f}%"], **x)
                else:
                    await run.enter(ev, sig, tk, self.risk_blocks(run), assessment=earnings.assessment(tk, side, late),
                                    extra=x)
                await self.save(run)
        except Exception:                                              # una falla no tira al operador
            log.exception("falló la entrada por resultados (CIK %s)", cik)

    async def _book_or_none(self, sym: str):
        try:
            return await self.books.book(sym)
        except MarketDataError:
            return None

    async def _ticker_or_none(self, sym: str):
        try:
            return await self.market.ticker(self.session, sym)
        except MarketDataError:
            return None

    async def _delisting(self, ing, coin: str, category: str = delistings.CATEGORY) -> None:
        """Corto apenas llega un aviso negativo de Binance (deslistado o etiqueta de vigilancia), si el precio todavía
        no cayó (decision/delistings.py): si ya cayó más que lo medido desde el aviso, llegamos tarde y no se entra."""
        rule = delistings.BY_CATEGORY[category]
        try:
            if self.market.universe.kind(coin) != CRYPTO:              # sin perpetuo de cripto en Binance: nada
                return
            t0 = ing.item.published_at or ing.item.received_at
            watch = Stopwatch(ing.item.received_at, self.clock)
            async with self.lock:
                watch.mark("candado")
                run = self.run()
                now, sym = run.now, self.market.symbol(coin)
                ev, sig = self._event(ing), delistings.signal(coin, category)
                late = now - t0
                x = {**notice_times(ing), "_reloj": watch,
                     "params": {"max_late_s": delistings.MAX_LATE_S, "max_drop": rule.max_drop}}
                if late > delistings.MAX_LATE_S:
                    run.record(ev, sig, sym, "no_entrar", [f"deslistado_tarde:{late:.0f}s"], **shadow.resolved(x))
                    await self.save(run)
                    return
                self.registry = await self.market.instruments(self.session, [sym], self.st["instruments"], now)
                run.reg = self.registry
                self.venue.registry = self.registry
                # Velas, libro y precio en un solo viaje a Binance (~0,3 s cada viaje, y el precio cae entre los 2 y
                # 5 s del aviso; el canal trae el aviso en ~1,5 s): la entrada usa el mismo libro.
                _, book, tick = await asyncio.gather(
                    run.store.load({sym: [(minute(t0) - 130 * MIN, minute(now))]}), self._book_or_none(sym),
                    self._ticker_or_none(sym))
                bar = (run.store.candles.get(sym) or {}).get(minute(t0))       # el minuto del aviso: (o, h, l, c)
                if not bar or book is None or book.mid is None or tick is None:
                    run.record(ev, sig, sym, "no_entrar", ["sin_datos:precio_del_aviso_o_libro"], **shadow.resolved(x))
                elif (drop := float(book.mid) / float(bar[0]) - 1) < -rule.max_drop:
                    run.record(ev, sig, sym, "no_entrar", [f"ya_cayo:{drop * 100:+.2f}%"],
                               **shadow.resolved({**x, **reference(float(bar[0]), book, drop)}))
                else:
                    await run.enter(ev, sig, coin, self.risk_blocks(run),
                                    assessment=delistings.assessment(coin, late, category), prefetched=(book, tick),
                                    extra={**x, **reference(float(bar[0]), book, drop)})
                    watch.mark("orden")
                    log.info("%s al instante (%s): %s", category, coin, watch.text())
                    if sym in self.st["positions"] and self.llm is not None and self.llm.enabled:
                        task = asyncio.create_task(self._ai_second_read(ing, coin, sym, category),
                                                   name=f"segunda-lectura-{coin}")
                        self.pending.add(task)
                        task.add_done_callback(self.pending.discard)
                await self.save(run)
        except Exception:                                              # una falla no tira al operador
            log.exception("falló la entrada por deslistado (%s)", coin)

    def _fast(self, ing, rule, t0: float | None) -> tuple[str, ...]:
        if getattr(self.market, "name", "") != "binance-futuros" or t0 is None:
            return ()
        _, coins = rule.notice(ing.item.source, ing.item.title)
        return coins if coins and self.clock.time() - t0 <= rule.MAX_LATE_S else ()

    def upbit_fast(self, ing) -> tuple[str, ...]:
        """Las monedas de un listado de Upbit que llegó a tiempo para la regla al instante (decision/upbit_listings.py:
        por Telegram, ≤ 3 s). Más tarde (el sondeo del sitio, ~8 s), lo opera la regla general de listados."""
        return self._fast(ing, upbit_listings, ing.item.published_at or ing.item.received_at)

    def binance_fast(self, ing) -> tuple[str, ...]:
        """Las monedas de un listado de Binance que llegó a tiempo para la regla al instante
        (decision/binance_listings.py: ≤ 2,2 s de publicado; sin la hora de publicación, no). Más tarde, la regla
        general de listados, con la IA."""
        return self._fast(ing, binance_listings, ing.item.published_at)

    async def _upbit_listing(self, ing, coin: str) -> None:
        """Largo apenas llega un listado de Upbit (decision/upbit_listings.py), si el precio todavía no subió más que el
        tope de Binance de los primeros segundos (+16 % desde la apertura del minuto del aviso)."""
        await self._listing_now(ing, coin, upbit_listings)

    async def _binance_listing(self, ing, coin: str) -> None:
        """Largo apenas llega un listado de Binance (decision/binance_listings.py), si llegó a ≤ 2,2 s de publicado y
        el precio no subió más de 8 % desde la apertura del minuto del aviso."""
        await self._listing_now(ing, coin, binance_listings)

    async def _listing_now(self, ing, coin: str, rule) -> None:
        """Un listado al instante (Upbit o Binance: `rule` es su módulo de decision/). Sin la IA y sin las alarmas de
        B-4 (el salto vacía el libro); protege el tope de precio de la orden. La IA lee después."""
        try:
            if self.market.universe.kind(coin) != CRYPTO:              # sin perpetuo de cripto en Binance: nada
                return
            t0 = ing.item.published_at or ing.item.received_at
            watch = Stopwatch(ing.item.received_at, self.clock)
            async with self.lock:
                watch.mark("candado")
                run = self.run()
                now, sym = run.now, self.market.symbol(coin)
                ev, sig = self._event(ing), rule.signal(coin)
                late = now - t0
                x = {**notice_times(ing), "_reloj": watch,
                     "params": {"max_late_s": rule.MAX_LATE_S, "max_rise": rule.MAX_RISE}}
                if late > rule.MAX_LATE_S:                             # el candado tardó: ya no es "al instante"
                    run.record(ev, sig, sym, "no_entrar", [f"{rule.CATEGORY}_tarde:{late:.1f}s"],
                               **shadow.resolved(x))
                    await self.save(run)
                    return
                self.registry = await self.market.instruments(self.session, [sym], self.st["instruments"], now)
                run.reg = self.registry
                self.venue.registry = self.registry
                watch.mark("contrato")
                _, book, tick = await asyncio.gather(
                    run.store.load({sym: [(minute(t0) - 130 * MIN, minute(now))]}), self._book_or_none(sym),
                    self._ticker_or_none(sym))
                watch.mark("velas, libro y precio")
                bar = (run.store.candles.get(sym) or {}).get(minute(t0))       # el minuto del aviso: (o, h, l, c)
                if not bar or book is None or book.mid is None or tick is None:
                    run.record(ev, sig, sym, "no_entrar", ["sin_datos:precio_del_aviso_o_libro"], **shadow.resolved(x))
                elif (rise := float(book.mid) / float(bar[0]) - 1) > rule.MAX_RISE:
                    run.record(ev, sig, sym, "no_entrar", [f"ya_subio:{rise * 100:+.2f}%"],
                               **shadow.resolved({**x, **reference(float(bar[0]), book, rise)}))
                else:
                    await run.enter(ev, sig, coin, self.risk_blocks(run),
                                    assessment=rule.assessment(coin, late), prefetched=(book, tick),
                                    relaxed=True, extra={**x, **reference(float(bar[0]), book, rise)})
                    watch.mark("orden")
                    log.info("%s al instante (%s): %s", rule.NAME, coin, watch.text())
                    if sym in self.st["positions"] and self.llm is not None and self.llm.enabled:
                        task = asyncio.create_task(self._ai_second_read(ing, coin, sym, rule.CATEGORY,
                                                                        rule.signal(coin)),
                                                   name=f"segunda-lectura-{coin}")
                        self.pending.add(task)
                        task.add_done_callback(self.pending.discard)
                await self.save(run)
        except Exception:                                              # una falla no tira al operador
            log.exception("falló la entrada por %s (%s)", rule.NAME, coin)

    async def _ai_second_read(self, ing, coin: str, sym: str, category: str, expected=None) -> None:
        """Segunda lectura de la IA, después de entrar (el "nivel 2 que aborta" del plan del 5-nov): la regla entró sin
        esperarla porque el precio se mueve en los primeros 2 a 5 s; si la IA no lee lo mismo para esta moneda (otra
        moneda, u otro tipo de aviso: no es un deslistado, una advertencia o un listado), se cierra la posición. Sin
        respuesta, queda lo medido."""
        try:
            expected = expected or delistings.signal(coin, category)
            asked = time.perf_counter()
            res = await self.llm.classify(ing.item.title, ing.item.body, expected)
            ai = res.signal
            confirmed = (ai is not None and ai.polarity * expected.polarity > 0
                         and coin_key(coin) in {coin_key(a) for a in ai.assets})
            await self._ai_variant(ing, sym, category, time.perf_counter() - asked, confirmed, ai is not None,
                                   expected.polarity)
            if ai is None or confirmed:
                return
            async with self.lock:
                if sym not in self.st["positions"]:
                    return
                run = self.run()
                await run.close(sym, "la_ia_no_confirma")
                await self.save(run)
            log.warning("la IA no confirmó el aviso de %s (%s, polaridad %+.2f, activos %s): cerré la posición",
                        coin, ai.category, ai.polarity, ",".join(ai.assets))
        except Exception:                                              # una falla no tira al operador
            log.exception("falló la segunda lectura de la IA (%s)", coin)

    async def _ai_variant(self, ing, sym: str, category: str, ai_s: float, confirmed: bool, answered: bool,
                          polarity: float) -> None:
        """La prueba A/B que pidió el dueño (5-nov): ¿y si se esperaba a la IA? Al llegar su respuesta se anota cuánto
        tardó y el precio al que se habría entrado ahí (`variante-ia.jsonl`, al lado de las operaciones). La entrada al
        instante (A) es la operación real; la de la IA (B) se compara con su misma salida: retorno de B ≈ (salida de A /
        precio de B) − 1, solo si la IA confirmó (`estudios/upbit_ia_o_rapido.py` lo midió con los 31 listados)."""
        try:
            book = await self._book_or_none(sym)
            pos = self.st["positions"].get(sym) or {}
            px = None if book is None else (book.best_ask if polarity > 0 else book.best_bid)
            row = {"ts": round(self.clock.time(), 3), "symbol": sym, "categoria": category, "evento": ing.item.id,
                   "publicado": ing.item.published_at, "ia_s": round(ai_s, 3), "contesto": answered,
                   "confirma": confirmed, "entrada_al_instante": pos.get("avg_price"),
                   "precio_con_la_ia": None if px is None else str(px)}
            path = self.base / "variante-ia.jsonl"

            def write():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

            await asyncio.to_thread(write)
        except Exception:                                              # la medición nunca frena al operador
            log.warning("no pude anotar la variante con la IA (%s)", sym, exc_info=True)

    async def check_launches(self) -> None:
        """Perpetuos de cripto nuevos de Binance (decision/launches.py): cada uno se decide a los 15 minutos."""
        if getattr(self.market, "name", "") != "binance-futuros" or not hasattr(self, "st"):
            return
        from .marketdata import binance_futures as bfd
        try:
            cat = await bfd.catalog(self.session, base=self.market.base)
        except MarketDataError:
            return
        now = self.clock.time()
        seen = self.st.setdefault("lanzamientos", {})
        for sym in [s for s, t in seen.items() if now - t > 86_400]:      # se olvidan al día
            del seen[sym]
        for sym, t0 in launches.new_contracts(cat, now, seen):
            seen[sym] = t0
            task = asyncio.create_task(self._launch(sym, t0), name=f"lanzamiento-{sym}")
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)

    async def _launch(self, sym: str, t0: float) -> None:
        """Corto a los 15 minutos del inicio si el precio quedó en o debajo del primero (decision/launches.py)."""
        try:
            wait = t0 + launches.ENTRY_DELAY_S - self.clock.time()
            if wait > 0:
                await asyncio.sleep(wait)
            async with self.lock:
                run = self.run()
                now, asset = run.now, sym[:-4]
                ev, sig = launches.event(sym, t0, now), launches.signal(asset)
                late = now - t0
                if late > launches.MAX_LATE_S:
                    run.record(ev, sig, sym, "no_entrar", [f"lanzamiento_tarde:{late:.0f}s"])
                    await self.save(run)
                    return
                self.registry = await self.market.instruments(self.session, [sym], self.st["instruments"], now)
                run.reg = self.registry
                self.venue.registry = self.registry
                await run.store.load({sym: [(minute(t0), minute(now))]})
                first = (run.store.candles.get(sym) or {}).get(minute(t0))     # la primera vela: (o, h, l, c)
                try:
                    book = await self.books.book(sym)
                except MarketDataError:
                    book = None
                if not first or book is None or book.mid is None:
                    run.record(ev, sig, sym, "no_entrar", ["sin_datos:primer_precio_o_libro"])
                elif (rise := float(book.mid) / float(first[0]) - 1) > launches.MAX_RISE:
                    run.record(ev, sig, sym, "no_entrar", [f"no_bajo:{rise * 100:+.2f}%"],
                               **reference(float(first[0]), book, rise))
                else:
                    await run.enter(ev, sig, asset, self.risk_blocks(run),
                                    assessment=launches.assessment(asset, late),
                                    extra={**reference(float(first[0]), book, rise),
                                           "params": {"max_rise": launches.MAX_RISE}})
                await self.save(run)
        except Exception:                                              # una falla no tira al operador
            log.exception("falló la entrada por lanzamiento (%s)", sym)

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
                shadow.track(meta, px, side, run.now)                     # mejora continua: pico, piso y tiempos
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
                f"💵 Empecé a operar con DINERO REAL en futuros {self.venue_es}, con todo el saldo de la cuenta: "
                f"{usdt_es(equity, signed=False)}. Si ganamos, opero con lo que haya. Cada operación te llega marcada "
                "como DINERO REAL. Para frenar todo: python -m tradingagent cerrar-todo", silent=False))
        elif abs(delta) >= 1:
            await self.send_alert(system_alert(
                f"💵 Cambió el saldo de la cuenta de futuros sin operar ({usdt_es(delta)}): ahora opero con "
                f"{usdt_es(equity, signed=False)}.", silent=True))

    async def housekeeping(self) -> None:
        """Saldo real, spreads (z-score), frenos con el equity marcado, funding y estado guardado."""
        await self.refresh_universe()                                  # de la caché salvo cada 6 h
        if self.clock.time() - self._verdict_at >= 600:
            await self.refresh_verdicts()
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
        """Dinero real: si las posiciones del exchange no coinciden con las del programa, no se abre nada nuevo."""
        if not hasattr(self.venue, "reconcile"):
            return
        before = self.venue.blocked
        await self.venue.reconcile()
        now = self.venue.blocked                  # el motivo, no el texto que devuelve (un corte dice "sin respuesta")
        what = "del SIMULACRO" if self.simulated else f"de DINERO REAL en {self.venue_es}"
        if now == before:
            return
        if now.startswith("exchange_sin_respuesta"):
            await self.send_alert(system_alert(                         # un corte de red: se reintenta solo
                f"⚠️ {self.venue_es} no responde (o se cortó internet): no abro nada nuevo hasta que vuelva. Lo "
                "abierto tiene su stop de emergencia en el exchange.", silent=True))
        elif "diferencias" in now:
            await self.send_alert(system_alert(
                f"⚠️ Las posiciones {what} no coinciden con las del programa: no abro nada nuevo hasta que "
                f"coincidan. ¿Abriste o cerraste algo a mano en {self.venue_es}?", silent=False))
        elif "sin_stop" in now:
            syms = now.split("sin_stop:", 1)[1].split(";")[0]
            await self.send_alert(system_alert(
                f"⚠️ Una posición {what} quedó sin su stop de emergencia en el exchange ({syms}): no abro nada nuevo "
                "y lo vuelvo a poner cada 30 s. Si no se arregla, cerrala con /cerrar SI.", silent=False))
        elif now:
            await self.send_alert(system_alert(f"⚠️ No abro nada nuevo {what}: {now}", silent=False))
        else:
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
        calib = None
        try:                                                           # acierto y velocidad, de toda la historia
            from .research.calibracion import report
            rep = await asyncio.to_thread(report, self.base, self.home / "journal")
            calib = {"acierto": rep["acierto"], "cerradas": rep["cerradas"],
                     "reaccion_s": (rep["reaccion_s"] or {}).get("mediana"), "por_categoria": rep["por_categoria"]}
        except Exception:                                              # el resumen sale igual
            log.warning("no pude calcular la calibración del resumen", exc_info=True)
        await self.send_alert(daily_alert(
            last, mode=self.market.label, closed=s["closed"], won=s["won"], pnl=s["pnl"],
            equity=self.st.get("equity") or self.st["cash"], initial=self.st["initial"],
            total_closed=self.st["stats"]["operaciones"], llm=llm, calib=calib))

    async def watch(self, stop: asyncio.Event) -> None:
        last_house = last_launch = 0.0
        while not stop.is_set():
            try:
                await self.watch_once()
                if time.monotonic() - last_house >= HOUSEKEEP_S:
                    last_house = time.monotonic()
                    await self.housekeeping()
                if time.monotonic() - last_launch >= LAUNCH_POLL_S:
                    last_launch = time.monotonic()
                    await self.check_launches()
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
            out.append(f"  · {p['side']} {asset_es(sym)} desde {p['avg_price']}, límite {p['stop']}"
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
