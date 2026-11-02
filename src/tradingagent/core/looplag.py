"""Medidor del retraso del bucle de eventos: la prueba objetiva de que nada lo bloquea.

Duerme `interval` y mide cuánto de más tardó en despertar. Si algo bloquea el bucle (una llamada de red
síncrona, un parseo pesado sin hilo aparte), el retraso sube y se ve en el latido. Nota: en Windows el
temporizador del bucle tiene una granularidad de ~15 ms, así que ese es el piso normal allí.
"""
import asyncio
from collections import deque


def percentile(values, q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(q * (len(s) - 1))))
    return s[k]


class LoopLagMonitor:
    def __init__(self, interval: float = 0.05, window: int = 1200):
        self.interval = interval
        self.samples: deque[float] = deque(maxlen=window)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            t0 = loop.time()
            await asyncio.sleep(self.interval)
            self.samples.append(max(0.0, loop.time() - t0 - self.interval))

    def stats(self) -> dict:
        s = list(self.samples)
        return {"p50_ms": round(percentile(s, 0.5) * 1000, 2), "p99_ms": round(percentile(s, 0.99) * 1000, 2),
                "max_ms": round(max(s, default=0.0) * 1000, 2), "n": len(s)}
