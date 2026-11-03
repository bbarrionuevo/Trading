"""Latido: cada N segundos una línea en consola y el estado completo en TRADING_HOME/state/heartbeat.json.

La línea dice de un vistazo si el agente está sano: fuentes, noticias, lag del bucle, cartera, alertas
y diario. El JSON (escrito en un hilo para no tocar el bucle) lo puede leer un tablero o una persona.
"""
import asyncio
import logging
import time

from .. import paths

log = logging.getLogger("tradingagent.heartbeat")


class Heartbeat:
    def __init__(self, collect, *, interval_s: float = 15.0, state_file=None, printer=print):
        self.collect, self.interval, self.printer = collect, interval_s, printer
        self.state_file = state_file or paths.state("heartbeat.json")
        self.started = time.time()
        self.beats = 0

    @staticmethod
    def line(s: dict) -> str:
        src, news, lag = s.get("sources", {}), s.get("news", {}), s.get("loop_lag", {})
        pf, al, jr = s.get("portfolio", {}), s.get("alerts", {}), s.get("journal", {})
        failing = ",".join(src.get("failing", [])[:4]) or "-"
        return (f"♥ {time.strftime('%H:%M:%S')} {s.get('mode', '?')} up {s.get('uptime_min', 0):.0f}m · "
                f"fuentes {src.get('ok', 0)}/{src.get('total', 0)} (fallan: {failing}) · "
                f"noticias ok {news.get('aceptadas', 0)} stale {news.get('stale', 0)} dup "
                f"{news.get('duplicadas', 0)} ruido {news.get('ruido', 0)} · lag p99 {lag.get('p99_ms', 0)} ms · "
                f"equity {pf.get('equity', '?')} · alertas {al.get('health', '?')} "
                f"(cola {al.get('queued', 0)}) · diario {jr.get('written', 0)}")

    async def beat(self) -> dict:
        s = self.collect()
        s["uptime_min"] = round((time.time() - self.started) / 60, 1)
        s["ts"] = time.time()
        self.beats += 1
        await asyncio.to_thread(paths.write_json_atomic, self.state_file, s)    # primero el estado (tablero)
        try:
            self.printer(self.line(s))
        except Exception:                                         # la consola nunca frena el latido
            log.warning("no se pudo mostrar el latido en la consola", exc_info=True)
        return s

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.beat()
            except Exception:                                     # el latido nunca tumba al agente
                log.exception("latido falló")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass
        try:                                                      # último latido: el estado con el que cerró
            await self.beat()
        except Exception:                                         # noqa: BLE001 (cerrar nunca falla por el latido)
            log.warning("no se pudo escribir el último latido")
