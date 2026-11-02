"""Bus de eventos asíncrono con colas acotadas: publicar NUNCA bloquea ni espera a un consumidor lento.

Cada suscriptor tiene su propia cola. Si se llena, aplica su política:
- "drop_oldest": descarta el mensaje más viejo (noticias, telemetría: lo nuevo vale más);
- "drop_new": rechaza el nuevo y lo cuenta (cuando el orden importa más que la frescura).
Las pérdidas se cuentan por suscriptor y aparecen en el latido. Se usa solo desde el hilo del bucle de
eventos; para publicar desde otro hilo: loop.call_soon_threadsafe(bus.publish, topic, msg).
"""
import asyncio
from collections import defaultdict

POLICIES = ("drop_oldest", "drop_new")


class Subscription:
    def __init__(self, topic: str, name: str, maxsize: int, policy: str):
        if policy not in POLICIES:
            raise ValueError(f"política desconocida: {policy}")
        if maxsize < 1:
            raise ValueError("la cola necesita lugar para al menos un mensaje")
        self.topic, self.name, self.policy = topic, name, policy
        self.queue: asyncio.Queue = asyncio.Queue(maxsize)
        self.delivered = self.dropped = 0
        self.closed = False

    def offer(self, msg) -> bool:
        if self.closed:
            return False
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.policy == "drop_new":
                return False
            self.queue.get_nowait()
            self.queue.put_nowait(msg)
        self.delivered += 1
        return True

    async def get(self):
        return await self.queue.get()

    def get_nowait(self):
        return self.queue.get_nowait()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed and self.queue.empty():
            raise StopAsyncIteration
        return await self.queue.get()

    def stats(self) -> dict:
        return {"depth": self.queue.qsize(), "max": self.queue.maxsize, "delivered": self.delivered,
                "dropped": self.dropped}


class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Subscription]] = defaultdict(list)
        self.published: dict[str, int] = defaultdict(int)

    def subscribe(self, topic: str, name: str, maxsize: int = 1000, policy: str = "drop_oldest") -> Subscription:
        sub = Subscription(topic, name, maxsize, policy)
        self._subs[topic].append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        sub.closed = True
        if sub in self._subs.get(sub.topic, []):
            self._subs[sub.topic].remove(sub)

    def publish(self, topic: str, msg) -> int:
        """Entrega a todos los suscriptores del tópico sin esperar. Devuelve a cuántos llegó."""
        self.published[topic] += 1
        return sum(sub.offer(msg) for sub in self._subs.get(topic, ()))

    def stats(self) -> dict:
        return {f"{t}/{s.name}": s.stats() for t, subs in self._subs.items() for s in subs}
