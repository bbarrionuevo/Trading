"""Peso usado de la API de Binance Futures, compartido por todo el agente (el límite es por IP: 2.400 por minuto, y lo
gastan el agente, la práctica, el simulacro y cualquier estudio que corra en la PC). Se lee de la cabecera
`X-MBX-USED-WEIGHT-1M` de cada respuesta de Binance. Pasado el 429 viene el 418: la IP bloqueada hasta 3 días, y con
eso no se opera nada (lo recomiendan los dos informes de investigación del 5-nov, docs/PLAN.md).

- Si el peso del minuto pasa el 80 %, lo que no es urgente (catálogo, volúmenes, intervalos de funding) espera al
  minuto siguiente. Lo urgente (libros, velas y precios de una entrada; órdenes y salidas) nunca espera.
- Pasado el 90 %, un aviso en el log por minuto.
"""
import asyncio
import logging
import time

log = logging.getLogger("tradingagent.peso")
LIMIT = 2400
BRAKE = 0.80
WARN = 0.90


class WeightMonitor:
    def __init__(self, limit: int = LIMIT):
        self.limit = limit
        self.used = 0
        self.minute: int | None = None
        self.max_seen = 0
        self.waits = 0
        self._warned_minute: int | None = None

    def observe(self, headers, now: float | None = None) -> None:
        """Toma el peso usado de las cabeceras de una respuesta de Binance (sin la cabecera, nada)."""
        raw = headers.get("X-MBX-USED-WEIGHT-1M") if headers is not None else None
        if raw is None:
            return
        try:
            used = int(raw)
        except (TypeError, ValueError):
            return
        now = time.time() if now is None else now
        minute = int(now // 60)
        if minute != self.minute or used >= self.used:          # en el mismo minuto, el mayor visto
            self.used, self.minute = used, minute
        self.max_seen = max(self.max_seen, used)
        if used >= self.limit * WARN and self._warned_minute != minute:
            self._warned_minute = minute
            log.warning("peso de la API de Binance al %d %% (%d de %d): freno lo que no es urgente",
                        round(100 * used / self.limit), used, self.limit)

    def busy(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.minute == int(now // 60) and self.used >= self.limit * BRAKE

    async def wait_if_busy(self, now_fn=time.time) -> None:
        """Lo que no es urgente espera a que empiece el minuto siguiente si el peso pasó el 80 %."""
        while self.busy(now_fn()):
            self.waits += 1
            await asyncio.sleep(60.5 - now_fn() % 60)

    def snapshot(self) -> dict:
        return {"usado": self.used, "limite": self.limit, "maximo_visto": self.max_seen, "esperas": self.waits}


MONITOR = WeightMonitor()                 # uno por proceso: la IP es una sola
