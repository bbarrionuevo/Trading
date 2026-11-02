"""Eventos del dominio que viajan por el bus. Inmutables: un consumidor no puede alterar lo que ve otro."""
import hashlib
from dataclasses import dataclass, field

# Nivel de confianza de una fuente (REGLAS B-1). 1: oficial (Fed, SEC, exchange); 2: medio establecido;
# 3: agregador, redes o fuente sin historial.
TIER_OFFICIAL, TIER_MEDIA, TIER_UNVERIFIED = 1, 2, 3


def stable_id(*parts: str, n: int = 20) -> str:
    return hashlib.sha256("\x1f".join(p or "" for p in parts).encode("utf-8")).hexdigest()[:n]


@dataclass(frozen=True, slots=True)
class NewsItem:
    id: str
    source: str                         # id de la fuente, p. ej. "fed.press"
    tier: int
    kind: str                           # rss | webhook | replay
    title: str
    body: str = ""
    url: str = ""                       # canónica (sin parámetros de seguimiento)
    published_at: float | None = None   # época UTC según la fuente; None si no la informa
    received_at: float = 0.0            # época UTC en que llegó al agente
    categories: tuple[str, ...] = ()
    assets_hint: tuple[str, ...] = ()   # activos que la FUENTE asocia (metadatos, no NLP)
    category_hint: str = ""             # macro | regulatorio | cripto | hack | earnings ... (de la fuente)
    guid: str = ""
    lang: str = ""


@dataclass(frozen=True, slots=True)
class DedupResult:
    kind: str                           # new | exact | near
    cluster_id: str
    similarity: float = 1.0
    cluster_size: int = 1
    distinct_sources: int = 1
    first_source: str = ""


@dataclass(frozen=True, slots=True)
class NoiseResult:
    score: float
    is_noise: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IngestedNews:
    """Una noticia con el veredicto de la ingesta. `accepted` = pasa a la etapa de inteligencia."""
    item: NewsItem
    age_s: float | None                 # received_at - published_at (None si la fuente no da hora)
    freshness: str                      # fresh | stale | unknown | future
    dedup: DedupResult
    noise: NoiseResult
    accepted: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    backlog: bool = False               # vino en la primera lectura de la fuente: historia, no evento

    def summary(self) -> dict:
        it = self.item
        return {"id": it.id, "source": it.source, "tier": it.tier, "title": it.title[:200], "url": it.url,
                "published_at": it.published_at, "received_at": it.received_at, "age_s": self.age_s,
                "freshness": self.freshness, "dedup": self.dedup.kind, "cluster": self.dedup.cluster_id,
                "noise": self.noise.score, "accepted": self.accepted, "reasons": list(self.reasons),
                "backlog": self.backlog}


@dataclass(frozen=True, slots=True)
class Corroboration:
    """Otra fuente publicó la misma historia: sube la confianza del evento original (etapa 2)."""
    cluster_id: str
    item: NewsItem
    similarity: float
    distinct_sources: int
