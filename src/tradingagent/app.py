"""Ensamblado del agente: ingesta → inteligencia (clasificador + Claude si duda) → decisión y riesgo →
ejecución paper (live.py: futuros de Binance por defecto), con diario, latido y alertas. Cada pieza corre en
su propia tarea; las alertas y el diario en hilos. En segundo plano, el recolector aprende cada 15 min.

Arranque: carga TRADING_HOME/.env y config.yaml, abre el diario, lanza el despachador de alertas (que
hace su prueba de inicio y manda el mensaje "Agente iniciado" por Telegram sin que el motor lo espere),
y empieza a leer fuentes. Ctrl+C (o `stop`) cierra todo en orden y deja el diario escrito. El supervisor
(supervisor.py) pide la misma parada con calma creando `TRADING_HOME/state/parar` (para actualizarse).
"""
import asyncio
import logging
import random
import signal
import time
from decimal import Decimal as D

import aiohttp

from . import __version__, paths, safety
from . import limits as limits_mod
from .alerts.dispatcher import AlertDispatcher
from .alerts.model import startup_alert
from .alerts.telegram import LogTransport, TelegramTransport, api_base
from .core.bus import EventBus
from .core.clock import SystemClock
from .core.looplag import LoopLagMonitor
from .execution.instruments import InstrumentRegistry
from .execution.paper import LatencyModel, PaperConfig, PaperVenue
from .execution.portfolio import Portfolio
from .execution.service import ExecutionService
from .ingestion.catalog import DEFAULT_SOURCES, resolve
from .ingestion.dedup import DedupIndex
from .ingestion.engine import TOPIC_ACCEPTED, IngestionEngine
from .ingestion.noise import NoiseConfig, NoiseFilter
from .ingestion.sources import PollingSource, SourceSpec, with_overrides
from .ingestion.webhook import WebhookSource
from .marketdata.peso import MONITOR as PESO
from .marketdata.providers import RestBookProvider
from .telemetry.heartbeat import Heartbeat
from .telemetry.journal import Journal

log = logging.getLogger("tradingagent")
STOP_FILE, STOP_POLL_S = "parar", 1.0


def build_sources(cfg: dict, env, limits: dict | None = None) -> tuple[list[SourceSpec], dict[str, str]]:
    """(fuentes habilitadas, {id: motivo} de las apagadas). La edad máxima de cada una se recorta al sobre."""
    ing = cfg["ingestion"]
    limits = limits if limits is not None else limits_mod.load()
    specs = list(DEFAULT_SOURCES)
    for s in ing.get("extra_sources") or []:
        specs.append(SourceSpec(id=s["id"], url=s["url"], tier=int(s.get("tier", 2)),
                                interval_s=float(s.get("interval_s", 60)), max_age_s=float(s.get("max_age_s", 300)),
                                category=s.get("category", "news"), assets=tuple(s.get("assets", ()))))
    enabled, off = [], {}
    for spec in specs:
        o = (ing.get("sources") or {}).get(spec.id) or {}
        spec = with_overrides(spec, enabled=o.get("enabled"), interval_s=o.get("interval_s"),
                              max_age_s=o.get("max_age_s"))
        spec = with_overrides(spec, max_age_s=safety.news_max_age(spec.max_age_s, limits))
        spec, why = resolve(spec, env)
        if why:
            off[spec.id] = why
        else:
            enabled.append(spec)
    return enabled, off


def alert_transport_factory(cfg: dict, env, base_url: str | None = None):
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if cfg["alerts"].get("telegram") and token and chat:
        base = base_url or api_base(env)
        return lambda: TelegramTransport(token, chat, base_url=base)
    return LogTransport


def quiet_connection_resets(loop, context) -> None:
    """En Windows, cuando un servidor corta una conexión ya terminada, asyncio registra un ERROR inofensivo
    (`_ProactorBasePipeTransport._call_connection_lost`: WinError 10054). En la PC llenaba errores.jsonl y tapaba
    los errores de verdad: ese caso va a DEBUG; todo lo demás sigue igual."""
    if isinstance(context.get("exception"), ConnectionResetError) and \
            "_call_connection_lost" in str(context.get("message", "")):
        log.debug("conexión cerrada por el otro lado: %s", context.get("exception"))
        return
    loop.default_exception_handler(context)


VENUE_ES = {"binance-futuros": "Binance", "mexc-futuros": "MEXC"}


def binance_keys(env, *, demo: bool) -> tuple[str | None, str | None]:
    """Las claves de Binance: las de la DEMO (plata ficticia) o las reales. Nunca se mezclan."""
    if demo:
        return env.get("BINANCE_API_KEY_TEST"), env.get("BINANCE_API_SECRET_TEST")
    return env.get("BINANCE_API_KEY"), env.get("BINANCE_API_SECRET")


def arm_key(env, data: dict) -> str | None:
    """La clave con la que se armó: la huella del armado tiene que coincidir con ella."""
    from .execution.live import arming
    if arming.venue_of(data) == "binance-futuros":
        return binance_keys(env, demo=bool(data.get("simulacro")))[0]
    return env.get("MEXC_ACCESS_KEY")


class OwnerActions:
    """Lo que el dueño puede pedir por Telegram (alerts/commands.py): informar y bajar riesgo, nunca armar."""

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def status_text(self) -> str:
        from .execution.live import arming
        a, home = self.agent, paths.home()
        lines = ["📊 Estado"]
        if a.trader is not None:
            lines += a.trader.status_lines("Práctica")
        if a.real_trader is not None:
            lines += a.real_trader.status_lines("Simulacro" if a.real_trader.simulated else "💵 DINERO REAL")
        current = paths.read_json(home / arming.ARM_FILE, None) or {}
        ok, why, data = arming.status(home, arm_key(a.env, current))
        if data:
            left = (float(data.get("vence", 0)) - time.time()) / 86400
            lines.append(f"Armado en {VENUE_ES.get(arming.venue_of(data), arming.venue_of(data))} "
                         f"({'simulacro' if data.get('simulacro') else 'dinero real'}): "
                         + (f"opera, vence en {left:.0f} días" if ok else f"no abre nada nuevo ({why})"))
        else:
            lines.append("Dinero real: no armado")
        if (home / "KILL").exists():
            lines.append("⏸ En pausa (/seguir para volver)")
        return "\n".join(lines)

    async def disarm(self) -> str:
        from .execution.live import arming
        had = arming.disarm(paths.home())
        return ("🔒 Dinero real desarmado: no abro nada nuevo. Lo abierto sale normal con sus límites." if had
                else "No había nada armado.")

    async def close_all(self, why: str) -> str:
        from .execution.live import arming
        where = VENUE_ES.get(arming.venue_of(paths.read_json(paths.home() / arming.ARM_FILE, None) or {}), "el exchange")
        arming.disarm(paths.home())
        t = self.agent.real_trader
        if t is None:
            return "No hay operador de dinero real corriendo: no había nada que cerrar. Quedó desarmado."
        before, left = await t.close_all(why)
        if left:
            return (f"Cerré {before - left} de {before}. Quedan {left} abiertas: cerralas a mano en {where} o en la "
                    "PC con python -m tradingagent cerrar-todo. Quedó desarmado.")
        return f"✅ Cerré todo ({before} posiciones) y quedó desarmado. Para volver: armar-real en la PC."


class Agent:
    def __init__(self, cfg: dict, env, *, clock=None, sources: list[SourceSpec] | None = None, book_provider=None,
                 telegram_base_url: str | None = None, printer=print, trader_market=None, llm_base_url=None):
        self.cfg, self.env, self.clock = cfg, env, clock or SystemClock()
        self._sources_override, self._book_provider = sources, book_provider
        self._trader_market, self._llm_base_url = trader_market, llm_base_url
        self.trader = None
        self.real_trader = None                       # dinero real: solo si el dueño armó en esta PC (A-2)
        self.telegram_base_url, self.printer = telegram_base_url, printer
        self.stop_event = asyncio.Event()
        self.started = time.time()
        self.news_tasks: set[asyncio.Task] = set()     # noticias que el operador está evaluando ahora
        self.streams: list = []                        # fuentes empujadas (no se sondean)

    def stop(self) -> None:
        self.stop_event.set()

    async def _watch_stop_file(self, path) -> None:
        """Parada con calma pedida por el supervisor (archivo `state/parar`): cierra igual que Ctrl+C."""
        while not self.stop_event.is_set():
            if await asyncio.to_thread(path.exists):
                log.info("parada pedida por el supervisor: cierro en orden")
                self.stop()
                return
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=STOP_POLL_S)
            except TimeoutError:
                pass

    def collect(self) -> dict:
        """Estado para el latido (sin E/S)."""
        c = self.ingestion.stats()["counts"]
        health = [p.health for p in self.pollers]
        failing = [p.spec.id for p in self.pollers if p.health.state in ("degradada", "abierta")]
        return {"mode": self.cfg["mode"].upper(), "version": __version__,
                "sources": {"total": len(self.pollers), "ok": sum(h.state == "ok" for h in health),
                            "failing": failing, "off": self.sources_off},
                "news": {"aceptadas": c.get("aceptadas", 0), "stale": c.get("stale", 0),
                         "duplicadas": c.get("duplicado_exact", 0) + c.get("duplicado_near", 0),
                         "ruido": c.get("ruido", 0), "backlog": c.get("backlog", 0)},
                "dedup": self.ingestion.dedup.stats(), "loop_lag": self.lag.stats(), "peso_binance": PESO.snapshot(),
                "portfolio": self.venue.portfolio.snapshot(self.venue.marks), "paper": self.venue.summary()
                | {"portfolio": None}, "execution": self.execution.counts, "alerts": self.alerts.snapshot(),
                "journal": self.journal.snapshot(), "bus": self.bus.stats(),
                "streams": [s.snapshot() for s in self.streams],
                "trader": self.trader.snapshot() if self.trader is not None else None,
                "real": self.real_trader.snapshot() if self.real_trader is not None else None}

    async def _print_accepted(self) -> None:
        """Cada noticia aceptada se muestra en consola y, si el operador en vivo está prendido, se evalúa ya."""
        sub = self.bus.subscribe(TOPIC_ACCEPTED, "consola", maxsize=200)
        async for ev in sub:
            it = ev.item
            for trader in (self.trader, self.real_trader):      # no frena la lectura: cada noticia en su tarea
                if trader is not None:
                    t = asyncio.create_task(self._trade(ev, trader), name=f"noticia-{it.id[:8]}")
                    self.news_tasks.add(t)
                    t.add_done_callback(self.news_tasks.discard)
            age = f"{ev.age_s:.0f}s" if ev.age_s is not None else "s/h"
            try:
                self.printer(f"📰 [{it.source} n{it.tier} {age}] {it.title[:140]}")
            except Exception:                                    # la consola nunca frena una noticia
                log.warning("no se pudo mostrar una noticia en la consola", exc_info=True)

    async def _trade(self, ev, trader=None) -> None:
        try:
            await (trader or self.trader).on_news(ev)
        except Exception:                                    # una noticia mal procesada no tira el agente
            log.exception("falló la evaluación de %s", ev.item.id)

    def _build_commands(self):
        from .alerts.commands import POLL_S, Commands
        token, chat = self.env.get("TELEGRAM_BOT_TOKEN"), self.env.get("TELEGRAM_CHAT_ID")
        if not (self.cfg["alerts"].get("telegram") and self.cfg["alerts"].get("ordenes", True) and token and chat):
            return None
        transport = TelegramTransport(token, chat, base_url=self.telegram_base_url or api_base(self.env),
                                      timeout_s=POLL_S + 15)
        return Commands(transport, paths.home(), OwnerActions(self), chat_id=chat)

    async def _probe_loop(self, session, first_s: float = 90.0) -> None:
        """Una vez por día: qué fuentes candidatas se pueden leer desde esta PC (research/probe.py). El resultado
        (`logs/red.json`) lo publica el supervisor para que Claude sume fuentes que de verdad funcionan acá."""
        from .research.probe import EVERY_S, probe
        out = paths.home() / "logs" / "red.json"
        delay = first_s
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
            try:
                rep = await probe(session)
                await asyncio.to_thread(paths.write_json_atomic, out, rep)
            except Exception:                                        # la prueba nunca frena al agente
                log.warning("falló la prueba de red", exc_info=True)
            delay = EVERY_S

    def _build_streams(self, lim) -> list:
        """Fuentes empujadas: el canal oficial de anuncios de Binance (WebSocket), con una clave de SOLO LECTURA
        (`BINANCE_LECTURA_KEY` / `BINANCE_LECTURA_SECRET`). Sin esa clave queda apagado (y lo dice el latido)."""
        from .ingestion.binance_ws import SOURCE_ID, BinanceAnnouncements
        o = (self.cfg["ingestion"].get("sources") or {}).get(SOURCE_ID) or {}
        if o.get("enabled") is False:
            self.sources_off[SOURCE_ID] = "apagada en la configuración"
            return []
        key, secret = self.env.get("BINANCE_LECTURA_KEY"), self.env.get("BINANCE_LECTURA_SECRET")
        if not (key and secret):
            self.sources_off[SOURCE_ID] = "falta BINANCE_LECTURA_KEY y BINANCE_LECTURA_SECRET en TRADING_HOME/.env"
            return []
        return [BinanceAnnouncements(key, secret, self.ingestion.handle, self.clock,
                                     max_age_s=safety.news_max_age(float(o.get("max_age_s") or 600), lim))]

    def _build_trader(self, session, lim):
        from .intelligence.llm import LlmClassifier, api_key
        from .live import LiveTrader
        from .marketdata import mexc
        from .research.futures import BinanceFutures, MexcFutures
        from .research.shadow import BinanceSpot
        t = self.cfg["trader"]
        market = self._trader_market
        if market is None:
            if t["mercado"] == "binance-spot":
                market = BinanceSpot()
            elif t["mercado"] == "binance-futuros":
                market = BinanceFutures()
            else:
                keys = (self.env.get("MEXC_ACCESS_KEY"), self.env.get("MEXC_SECRET_KEY"))
                market = MexcFutures(readonly=mexc.ReadOnlyClient(session, *keys) if all(keys) else None)
        key = api_key(self.env) if t.get("llm", True) else None
        cap = float(lim["ai"]["max_llm_usd_per_day"])                  # techo del sobre: frena un error, no el uso
        if t.get("llm_usd_dia") is not None:
            cap = min(cap, float(t["llm_usd_dia"]))                     # el dueño puede bajarlo, nunca subirlo
        llm = LlmClassifier(key, limit_usd=cap, base_url=self._llm_base_url) if key else None
        return LiveTrader(market, session, self.clock, lim, alerts=self.alerts, journal=self.journal,
                          capital=float(t["capital_usdt"]), llm=llm)

    def _build_real_trader(self, session, lim):
        """El operador de DINERO REAL, en paralelo al de práctica: misma noticia, misma decisión. Existe solo si el
        dueño armó en esta PC (aunque esté vencido: así las posiciones abiertas salen normal).

        Armado como SIMULACRO (`armar-real --simulacro`): el mismo operador real completo, pero las órdenes se
        ejecutan contra el libro real de MEXC en un exchange simulado (dryrun.py): nunca sale una orden."""
        from .execution.live import arming
        from .execution.live.dryrun import DryRunClient
        from .execution.live.mexc_futures import TradingClient
        from .execution.live.venue import RealVenue
        from .live import LiveTrader
        from .research.futures import MexcFutures
        home = paths.home()
        data = paths.read_json(home / arming.ARM_FILE, None)
        if not isinstance(data, dict) or not self.cfg["trader"].get("real", True):
            return None
        if arming.venue_of(data) == "binance-futuros":
            return self._build_binance_real_trader(session, lim, data)
        sim = bool(data.get("simulacro"))
        keys = (self.env.get("MEXC_ACCESS_KEY"), self.env.get("MEXC_SECRET_KEY"))
        if not sim and not all(keys):
            return None
        lev = int(lim["per_trade"]["max_leverage"])
        market = MexcFutures(max_leverage=lev)
        market.label = "SIMULACRO futuros MEXC" if sim else "REAL futuros MEXC"
        base_dir = home / ("simulacro" if sim else "real") / market.name

        def venue(registry, books, pf, clock):
            client = (DryRunClient(books, session, capital=float(data["capital_usdt"]), clock=clock,
                                   state_path=base_dir / "exchange-simulado.json") if sim
                      else TradingClient(session, *keys))
            return RealVenue(client, session, registry, books, pf, clock, home=home, access_key=keys[0], limits=lim,
                             leverage=lev)

        return LiveTrader(market, session, self.clock, lim, alerts=self.alerts, journal=self.journal,
                          capital=float(data["capital_usdt"]) if sim else 0.0,     # real: todo el saldo (lo lee)
                          llm=self.trader.llm if self.trader else None,
                          venue_factory=venue, base_dir=base_dir, real=True, simulated=sim)

    def _build_binance_real_trader(self, session, lim, data):
        """Binance (desde el 4-nov-2026): el operador real con el venue de Binance. Tres modos:
        - real: la clave de trading, todo el saldo de la cuenta;
        - simulacro LOCAL (el de siempre desde el 4-nov): precios y libro de PRODUCCIÓN, órdenes simuladas en la PC
          (binance_dryrun.py). Nunca usa la clave de trading: solo la de lectura, para el saldo y la comisión reales;
        - simulacro DEMO: contra la demo de Binance (claves *_TEST): órdenes de verdad, plata ficticia.
        Las decisiones usan siempre los precios del Binance real."""
        from .execution.live.binance_dryrun import LocalDryRunClient
        from .execution.live.binance_futures import BASE, DEMO, BinanceClient
        from .execution.live.binance_venue import BinanceVenue
        from .live import LiveTrader
        from .marketdata import binance_futures as bfd
        from .research.futures import BinanceFutures
        home = paths.home()
        sim = bool(data.get("simulacro"))
        mode = (data.get("modo") or "demo") if sim else "real"
        lev = int(lim["per_trade"]["max_leverage"])
        if mode == "local":                                    # el capital real, 100 % aislado: sin clave de trading
            market = BinanceFutures(max_leverage=lev)
            market.label = "SIMULACRO futuros Binance"
            base_dir = home / "simulacro" / market.name
            rkey, rsecret = self.env.get("BINANCE_LECTURA_KEY"), self.env.get("BINANCE_LECTURA_SECRET")

            def local(registry, books, pf, clock):
                reader = BinanceClient(session, rkey, rsecret) if rkey and rsecret else None
                client = LocalDryRunClient(books, session, capital=float(data["capital_usdt"]), clock=clock,
                                           reader=reader, state_path=base_dir / "exchange-simulado.json")
                return BinanceVenue(client, session, registry, books, pf, clock, home=home, api_key="", limits=lim,
                                    leverage=lev, state_path=base_dir / "stops.json", simulated=True)

            return LiveTrader(market, session, self.clock, lim, alerts=self.alerts, journal=self.journal,
                              capital=float(data["capital_usdt"]), llm=self.trader.llm if self.trader else None,
                              venue_factory=local, base_dir=base_dir, real=True, simulated=True)
        key, secret = binance_keys(self.env, demo=sim)
        if not (key and secret):
            log.warning("armado de Binance sin %s: el operador real no arranca",
                        "BINANCE_API_KEY_TEST / BINANCE_API_SECRET_TEST" if sim else "BINANCE_API_KEY / BINANCE_API_SECRET")
            return None
        market = BinanceFutures(max_leverage=lev)
        market.label = "SIMULACRO futuros Binance (demo)" if sim else "REAL futuros Binance"
        base_dir = home / ("simulacro-demo" if sim else "real") / market.name

        def venue(registry, books, pf, clock):
            client = BinanceClient(session, key, secret, base=DEMO if sim else BASE)
            pricing = bfd.books(session, clock, DEMO) if sim else books
            return BinanceVenue(client, session, registry, pricing, pf, clock, home=home, api_key=key, limits=lim,
                                leverage=lev, state_path=base_dir / "stops.json", simulated=sim)

        return LiveTrader(market, session, self.clock, lim, alerts=self.alerts, journal=self.journal,
                          capital=float(data["capital_usdt"]) if sim else 0.0,     # real: todo el saldo (lo lee)
                          llm=self.trader.llm if self.trader else None,
                          venue_factory=venue, base_dir=base_dir, real=True, simulated=sim)

    async def run(self) -> None:
        cfg, env = self.cfg, self.env
        asyncio.get_running_loop().set_exception_handler(quiet_connection_resets)
        lim = limits_mod.load()                       # sobre inválido o más laxo que la línea base: no arranca
        home = paths.home()
        stop_file = home / "state" / STOP_FILE
        stop_file.unlink(missing_ok=True)             # un pedido viejo (de antes de arrancar) ya no vale
        self.journal = Journal(home / "journal", self.clock).start()
        self.bus = EventBus()
        enabled, self.sources_off = build_sources(cfg, env, lim)
        if self._sources_override is not None:
            enabled = self._sources_override
        boot = startup_alert(cfg["mode"].upper(), len(enabled), len(enabled) + len(self.sources_off), __version__)
        a = cfg["alerts"]
        self.alerts = AlertDispatcher(alert_transport_factory(cfg, env, self.telegram_base_url),
                                      max_queue=a["max_queue"], rate_per_s=a["rate_per_s"], burst=a["burst"],
                                      startup_alert=boot)
        self.alerts.start()
        self.journal.record("start", version=__version__, mode=cfg["mode"], sources=[s.id for s in enabled],
                            sources_off=self.sources_off)
        try:
            ing = cfg["ingestion"]
            dedup = DedupIndex(self.clock, ttl_s=ing["dedup"]["ttl_hours"] * 3600, max_items=ing["dedup"]["max_items"],
                               threshold=ing["dedup"]["threshold"])
            self.ingestion = IngestionEngine(self.bus, self.clock, dedup, NoiseFilter(NoiseConfig(ing["noise"]["threshold"])),
                                             journal=self.journal,
                                             default_max_age_s=safety.news_max_age(ing["default_max_age_s"], lim),
                                             max_future_skew_s=ing["max_future_skew_s"])
            self.lag = LoopLagMonitor()
            tasks: list[asyncio.Task] = []
            webhook = None
            async with aiohttp.ClientSession() as session:
                rng = random.Random()
                self.pollers = [PollingSource(s, session, self.clock, self.ingestion.on_source_items, rng=rng)
                                for s in enabled]
                p = cfg["paper"]
                books = self._book_provider or RestBookProvider(p["venue"], session, self.clock)
                self.venue = PaperVenue(InstrumentRegistry(), books, Portfolio(D(str(p["cash_usdt"]))), self.clock,
                                        PaperConfig(latency=LatencyModel(median_ms=p["latency_ms"]),
                                                    participation=D(str(p["participation"])),
                                                    max_book_age_s=min(p["max_book_age_s"], lim["market"]["max_book_age_s"]),
                                                    default_max_slippage_bps=safety.slippage_cap(p["default_max_slippage_bps"], lim),
                                                    stop_slippage_bps=safety.slippage_cap(p["stop_slippage_bps"], lim)),
                                        seed=p["seed"])
                self.execution = ExecutionService(self.venue, self.bus, self.clock, kill_file=paths.kill_file(),
                                                  journal=self.journal, alerts=self.alerts, mode=cfg["mode"].upper(),
                                                  max_orders_per_min=cfg["execution"]["max_orders_per_min"],
                                                  max_queue=cfg["execution"]["max_queue"], limits=lim)
                wh = ing["webhook"]
                if wh.get("enabled"):
                    webhook = WebhookSource(env.get("WEBHOOK_SECRET", ""), self.ingestion.handle, self.clock,
                                            host=wh["host"], port=wh["port"], senders=wh.get("senders") or {})
                    await webhook.start()
                if cfg["trader"].get("enabled", True):
                    self.trader = self._build_trader(session, lim)
                    try:
                        await self.trader.start()
                    except Exception:                        # sin mercado, el agente igual lee y registra
                        log.exception("no arrancó el operador en vivo")
                        self.trader = None
                    try:
                        self.real_trader = self._build_real_trader(session, lim)
                        if self.real_trader is not None:
                            await self.real_trader.start()
                    except Exception:                        # el de práctica sigue igual
                        log.exception("no arrancó el operador de DINERO REAL")
                        self.real_trader = None
                hb = Heartbeat(self.collect, interval_s=cfg["heartbeat_s"], printer=self.printer)
                tasks += [asyncio.create_task(p.run(self.stop_event), name=f"fuente-{p.spec.id}") for p in self.pollers]
                self.streams = self._build_streams(lim)
                tasks += [asyncio.create_task(st.run(self.stop_event, session), name="fuente-binance.ws")
                          for st in self.streams]
                tasks += [asyncio.create_task(self.execution.run(self.stop_event), name="ejecucion"),
                          asyncio.create_task(self.venue.run_monitor(self.stop_event), name="monitor-ordenes"),
                          asyncio.create_task(self.lag.run(), name="lag"),
                          asyncio.create_task(hb.run(self.stop_event), name="latido"),
                          asyncio.create_task(self._print_accepted(), name="consola"),
                          asyncio.create_task(self._watch_stop_file(stop_file), name="parada")]
                tasks.append(asyncio.create_task(self._probe_loop(session), name="red"))
                commands = self._build_commands()
                if commands is not None:                             # órdenes del dueño por Telegram
                    tasks.append(asyncio.create_task(commands.run(self.stop_event), name="ordenes"))
                if self.trader is not None:
                    from .live import learn_loop
                    tasks.append(asyncio.create_task(self.trader.watch(self.stop_event), name="vigilancia"))
                    if self.real_trader is not None:
                        tasks.append(asyncio.create_task(self.real_trader.watch(self.stop_event), name="vigilancia-real"))
                    if cfg["trader"].get("aprender", True):
                        tasks.append(asyncio.create_task(learn_loop(self.stop_event, cfg, env, lim, home / "datos"),
                                                         name="aprendizaje"))
                log.info("agente %s en modo %s: %d fuentes activas, %d apagadas", __version__, cfg["mode"], len(enabled),
                         len(self.sources_off))
                for sid, why in self.sources_off.items():
                    log.info("fuente %s apagada: %s", sid, why)
                try:
                    await self.stop_event.wait()
                finally:
                    self.stop_event.set()                 # si fue una cancelación, avisa a todas las tareas
                    if self.news_tasks:                   # una noticia a medio operar se termina y se guarda
                        await asyncio.wait(set(self.news_tasks), timeout=10)
                    for t in tasks:
                        if t.get_name() in ("lag", "consola", "aprendizaje", "red", "ordenes") or \
                                t.get_name().startswith("fuente-"):
                            t.cancel()
                    await asyncio.wait(tasks, timeout=15)
                    if webhook:
                        await webhook.stop()
                    if self.trader is not None and self.trader.llm is not None:
                        await self.trader.llm.close()
        finally:                                      # también con Ctrl+C en Windows (cancelación)
            self.journal.record("stop", uptime_s=round(time.time() - self.started, 1))
            await asyncio.to_thread(self.alerts.stop, 5)
            self.journal.stop()


def install_signal_handlers(agent: Agent) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, agent.stop)
        except (NotImplementedError, RuntimeError):            # Windows: Ctrl+C llega como KeyboardInterrupt
            pass
