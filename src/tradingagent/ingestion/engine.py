"""Motor de ingesta: punto único de entrada de todas las fuentes (RSS, webhook, reproducción).

Orden de evaluación de cada noticia:
1. validez (título);
2. deduplicación (siempre se registra: también lo viejo y lo ruidoso, para reconocer sus copias);
3. frescura: edad = llegada − publicación; más vieja que el máximo de su fuente ⇒ "stale" (REGLAS B-2:
   no se compra un techo con una noticia de hace 20 minutos). Sin hora de publicación, cuenta la
   primera vez que se la vio, y lo de la primera lectura de cada fuente es historia (backlog);
4. ruido.
Publica en el bus "news.accepted" (pasa a inteligencia), "news.rejected" (auditoría) y
"news.corroboration" (otra fuente dio una historia ya vista: más confianza para la etapa 2).
"""
import logging
from collections import Counter, defaultdict

from ..core.events import Corroboration, IngestedNews, NewsItem
from .dedup import DedupIndex
from .noise import NoiseFilter

log = logging.getLogger("tradingagent.ingestion")
TOPIC_ACCEPTED, TOPIC_REJECTED, TOPIC_CORROBORATION = "news.accepted", "news.rejected", "news.corroboration"


class IngestionEngine:
    def __init__(self, bus, clock, dedup: DedupIndex, noise: NoiseFilter, *, journal=None,
                 default_max_age_s: float = 300.0, max_future_skew_s: float = 120.0):
        self.bus, self.clock, self.dedup, self.noise, self.journal = bus, clock, dedup, noise, journal
        self.default_max_age, self.max_future_skew = default_max_age_s, max_future_skew_s
        self.counts: Counter = Counter()
        self.by_source: dict[str, Counter] = defaultdict(Counter)
        self.ingest_latency_ms: list[float] = []

    def freshness(self, item: NewsItem, max_age_s: float) -> tuple[str, float | None]:
        if item.published_at is None:
            return "unknown", None
        age = item.received_at - item.published_at
        if age < -self.max_future_skew:
            return "future", round(age, 1)
        if age > max_age_s:
            return "stale", round(age, 1)
        return "fresh", round(max(age, 0.0), 1)

    def evaluate(self, item: NewsItem, *, backlog: bool = False, max_age_s: float | None = None) -> IngestedNews:
        t0 = self.clock.monotonic()
        reasons = []
        dedup = self.dedup.check(item)
        fresh, age = self.freshness(item, max_age_s or self.default_max_age)
        noise = self.noise.score(item)
        if not item.title:
            reasons.append("sin_titulo")
        if backlog:
            reasons.append("backlog")
        if dedup.kind != "new":
            reasons.append(f"duplicado_{dedup.kind}")
        if fresh == "stale":
            reasons.append("stale")
        elif fresh == "future":
            log.warning("%s: hora de publicación %.0f s en el futuro (reloj o zona horaria de la fuente)",
                        item.source, -age)
        if noise.is_noise:
            reasons.append("ruido")
        ev = IngestedNews(item=item, age_s=age, freshness=fresh, dedup=dedup, noise=noise,
                          accepted=not reasons, reasons=tuple(reasons), backlog=backlog)
        self.ingest_latency_ms.append((self.clock.monotonic() - t0) * 1000)
        del self.ingest_latency_ms[:-1000]
        return ev

    async def handle(self, item: NewsItem, *, backlog: bool = False, max_age_s: float | None = None) -> IngestedNews:
        ev = self.evaluate(item, backlog=backlog, max_age_s=max_age_s)
        src = self.by_source[item.source]
        src["total"] += 1
        if ev.accepted:
            self.counts["aceptadas"] += 1
            src["aceptadas"] += 1
            self.bus.publish(TOPIC_ACCEPTED, ev)
        else:
            for r in ev.reasons:
                self.counts[r] += 1
                src[r] += 1
            self.bus.publish(TOPIC_REJECTED, ev)
            if ev.dedup.kind != "new" and not backlog:
                self.bus.publish(TOPIC_CORROBORATION, Corroboration(ev.dedup.cluster_id, item, ev.dedup.similarity,
                                                                    ev.dedup.distinct_sources))
        if self.journal is not None and not backlog:
            self.journal.record("news", ev.summary())
        return ev

    async def on_source_items(self, spec, items: list[NewsItem], first: bool) -> None:
        """Callback de PollingSource: aplica el máximo de edad de esa fuente."""
        for it in items:
            await self.handle(it, backlog=first, max_age_s=spec.max_age_s)
        if first:
            log.info("fuente %s: %d ítems de historia registrados (no se operan)", spec.id, len(items))

    def stats(self) -> dict:
        lat = sorted(self.ingest_latency_ms)
        p99 = lat[int(0.99 * (len(lat) - 1))] if lat else 0.0
        return {"counts": dict(self.counts), "dedup": self.dedup.stats(), "eval_p99_ms": round(p99, 3)}
