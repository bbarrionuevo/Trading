"""Ensamblado del agente: ingesta → (inteligencia, etapa 2) → (riesgo, etapa 3) → ejecución paper,
con diario, latido y alertas. Cada pieza corre en su propia tarea; las alertas y el diario en hilos.

Arranque: carga TRADING_HOME/.env y config.yaml, abre el diario, lanza el despachador de alertas (que
hace su prueba de inicio y manda el mensaje "Agente iniciado" por Telegram sin que el motor lo espere),
y empieza a leer fuentes. Ctrl+C (o `stop`) cierra todo en orden y deja el diario escrito.
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
from .marketdata.providers import RestBookProvider
from .telemetry.heartbeat import Heartbeat
from .telemetry.journal import Journal

log = logging.getLogger("tradingagent")


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


class Agent:
    def __init__(self, cfg: dict, env, *, clock=None, sources: list[SourceSpec] | None = None, book_provider=None,
                 telegram_base_url: str | None = None, printer=print):
        self.cfg, self.env, self.clock = cfg, env, clock or SystemClock()
        self._sources_override, self._book_provider = sources, book_provider
        self.telegram_base_url, self.printer = telegram_base_url, printer
        self.stop_event = asyncio.Event()
        self.started = time.time()

    def stop(self) -> None:
        self.stop_event.set()

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
                "dedup": self.ingestion.dedup.stats(), "loop_lag": self.lag.stats(),
                "portfolio": self.venue.portfolio.snapshot(self.venue.marks), "paper": self.venue.summary()
                | {"portfolio": None}, "execution": self.execution.counts, "alerts": self.alerts.snapshot(),
                "journal": self.journal.snapshot(), "bus": self.bus.stats()}

    async def _print_accepted(self) -> None:
        """Hasta que exista la etapa 2, las noticias aceptadas se muestran en consola."""
        sub = self.bus.subscribe(TOPIC_ACCEPTED, "consola", maxsize=200)
        async for ev in sub:
            it = ev.item
            age = f"{ev.age_s:.0f}s" if ev.age_s is not None else "s/h"
            self.printer(f"📰 [{it.source} n{it.tier} {age}] {it.title[:140]}")

    async def run(self) -> None:
        cfg, env = self.cfg, self.env
        lim = limits_mod.load()                       # sobre inválido o más laxo que la línea base: no arranca
        home = paths.home()
        self.journal = Journal(home / "journal", self.clock).start()
        self.bus = EventBus()
        enabled, self.sources_off = build_sources(cfg, env, lim)
        if self._sources_override is not None:
            enabled = self._sources_override
        boot = startup_alert(cfg["mode"].upper(), len(enabled), len(enabled) + len(self.sources_off), __version__,
                             extra=f"Símbolos paper: {', '.join(cfg['paper']['symbols'])}")
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
                hb = Heartbeat(self.collect, interval_s=cfg["heartbeat_s"], printer=self.printer)
                tasks += [asyncio.create_task(p.run(self.stop_event), name=f"fuente-{p.spec.id}") for p in self.pollers]
                tasks += [asyncio.create_task(self.execution.run(self.stop_event), name="ejecucion"),
                          asyncio.create_task(self.venue.run_monitor(self.stop_event), name="monitor-ordenes"),
                          asyncio.create_task(self.lag.run(), name="lag"),
                          asyncio.create_task(hb.run(self.stop_event), name="latido"),
                          asyncio.create_task(self._print_accepted(), name="consola")]
                log.info("agente %s en modo %s: %d fuentes activas, %d apagadas", __version__, cfg["mode"], len(enabled),
                         len(self.sources_off))
                for sid, why in self.sources_off.items():
                    log.info("fuente %s apagada: %s", sid, why)
                try:
                    await self.stop_event.wait()
                finally:
                    self.stop_event.set()                 # si fue una cancelación, avisa a todas las tareas
                    for t in tasks:
                        if t.get_name() in ("lag", "consola") or t.get_name().startswith("fuente-"):
                            t.cancel()
                    await asyncio.wait(tasks, timeout=15)
                    if webhook:
                        await webhook.stop()
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
