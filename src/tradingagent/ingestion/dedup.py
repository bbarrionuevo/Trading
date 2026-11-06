"""Deduplicación en memoria con ventana de tiempo: exacta (URL canónica, título) y casi-duplicados
(MinHash + LSH sobre el título, verificado con Jaccard exacto).

Agrupa en "clusters" las publicaciones de una misma historia y cuenta cuántas fuentes DISTINTAS la
dieron: eso es corroboración, y la etapa de inteligencia la usa para subir la confianza. El umbral es
deliberadamente conservador: fusionar dos eventos distintos haría perder uno (peor que contar dos veces
una historia, que el riesgo ya acota con un trade por cluster). La similitud semántica con embeddings se
enchufa en la etapa 2 sobre los mismos clusters.

Dos reglas para no fusionar avisos distintos con la misma plantilla (6-nov: dos listados de Upbit, "솔라나(SOL) 신규
거래지원 안내 (KRW, BTC, USDT 마켓)" y el de ETH, se parecían 0,62 > 0,6 y el segundo se perdía; en los 180 días del
estudio pasó dos veces, KMNO y BSB a los 45 min, RSR y GTC a las 5 h):
- si los títulos nombran otros tickers (palabras en mayúsculas: SOL, ETH, ALPACA...), son otra historia;
- dos avisos oficiales (nivel 1) de la misma fuente con distinto enlace son dos avisos.
"""
import hashlib
import random
import re
from collections import OrderedDict
from dataclasses import dataclass, field

from ..core.events import DedupResult, NewsItem
from .normalize import canonical_url, title_key, tokens

_P = (1 << 61) - 1                     # primo de Mersenne para las permutaciones de MinHash
_TICKER = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,9}(?![A-Za-z0-9])")


def tickers(title: str) -> frozenset[str]:
    """Las palabras en mayúsculas del título (tickers y siglas): si difieren, no es la misma historia."""
    return frozenset(_TICKER.findall(title or ""))


def _h64(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "little")


def shingles(title: str) -> frozenset[str]:
    """Palabras y pares de palabras del título: robusto a reordenamientos leves y a sinónimos sueltos."""
    w = tokens(title)
    return frozenset(w) | frozenset(f"{a} {b}" for a, b in zip(w, w[1:], strict=False))


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class _Entry:
    id: str
    cluster: str
    source: str
    seen_at: float
    shingles: frozenset
    bands: tuple
    url_key: str
    title_key: str
    tickers: frozenset = frozenset()


@dataclass
class _Cluster:
    id: str
    first_source: str
    sources: set = field(default_factory=set)
    size: int = 0


class DedupIndex:
    def __init__(self, clock, ttl_s: float = 6 * 3600, max_items: int = 20000, num_perm: int = 64,
                 bands: int = 16, threshold: float = 0.6, seed: int = 7):
        if num_perm % bands:
            raise ValueError("num_perm tiene que ser múltiplo de bands")
        self.clock, self.ttl, self.max_items, self.threshold = clock, ttl_s, max_items, threshold
        self.rows = num_perm // bands
        rng = random.Random(seed)
        self._a = [rng.randrange(1, _P) for _ in range(num_perm)]
        self._b = [rng.randrange(0, _P) for _ in range(num_perm)]
        self.entries: OrderedDict[str, _Entry] = OrderedDict()
        self.clusters: dict[str, _Cluster] = {}
        self.by_url: dict[str, str] = {}
        self.by_title: dict[str, str] = {}
        self.buckets: dict[tuple, set[str]] = {}
        self.counts = {"new": 0, "exact": 0, "near": 0, "evicted": 0}

    # -- MinHash ------------------------------------------------------------------------------
    def _signature(self, sh: frozenset) -> list[int]:
        hashed = [_h64(s) for s in sh] or [0]
        return [min((a * x + b) % _P for x in hashed) for a, b in zip(self._a, self._b, strict=True)]

    def _bands(self, sig: list[int]) -> tuple:
        r = self.rows
        return tuple((i, hash(tuple(sig[i * r:(i + 1) * r]))) for i in range(len(sig) // r))

    # -- ventana ------------------------------------------------------------------------------
    def _evict(self) -> None:
        now = self.clock.time()
        while self.entries:
            eid, e = next(iter(self.entries.items()))
            if now - e.seen_at <= self.ttl and len(self.entries) < self.max_items:   # deja lugar al nuevo
                break
            self.entries.popitem(last=False)
            self.counts["evicted"] += 1
            for b in e.bands:
                ids = self.buckets.get(b)
                if ids:
                    ids.discard(eid)
                    if not ids:
                        del self.buckets[b]
            if e.url_key and self.by_url.get(e.url_key) == eid:
                del self.by_url[e.url_key]
            if e.title_key and self.by_title.get(e.title_key) == eid:
                del self.by_title[e.title_key]
            c = self.clusters.get(e.cluster)
            if c:
                c.size -= 1
                if c.size <= 0:
                    del self.clusters[e.cluster]

    # -- API ----------------------------------------------------------------------------------
    def check(self, item: NewsItem) -> DedupResult:
        """Clasifica la noticia (new/exact/near) y la registra en la ventana."""
        self._evict()
        if item.id in self.entries:                       # el mismo ítem otra vez: no cuenta como fuente nueva
            c = self.clusters[self.entries[item.id].cluster]
            self.counts["exact"] += 1
            return DedupResult(kind="exact", cluster_id=c.id, cluster_size=c.size,
                               distinct_sources=len(c.sources), first_source=c.first_source)
        url_key = canonical_url(item.url) if item.url else ""
        tkey = title_key(item.title)
        tkey = tkey if len(tkey) >= 12 else ""            # títulos muy cortos no identifican una historia
        sh = shingles(item.title)
        kind, similarity, match = "new", 1.0, None
        if url_key and url_key in self.by_url:
            kind, match = "exact", self.by_url[url_key]
        elif tkey and tkey in self.by_title:
            kind, match = "exact", self.by_title[tkey]
        sig_bands = self._bands(self._signature(sh)) if sh else ()
        if kind == "new" and len(sh) >= 3:
            best, best_id = 0.0, None
            candidates = set()
            for b in sig_bands:
                candidates |= self.buckets.get(b, set())
            tk = tickers(item.title)
            for cid in candidates:
                e = self.entries[cid]
                if e.tickers != tk:                       # otros activos: otra historia (misma plantilla)
                    continue
                if item.tier == 1 and e.source == item.source and url_key and e.url_key and e.url_key != url_key:
                    continue                              # dos avisos oficiales distintos de la misma fuente
                s = jaccard(sh, e.shingles)
                if s > best:
                    best, best_id = s, cid
            if best_id is not None and best >= self.threshold:
                kind, similarity, match = "near", best, best_id
        if match is not None:
            cluster = self.clusters[self.entries[match].cluster]
        else:
            cluster = _Cluster(id=item.id, first_source=item.source)
            self.clusters[cluster.id] = cluster
        cluster.sources.add(item.source)
        cluster.size += 1
        if item.id not in self.entries:
            e = _Entry(item.id, cluster.id, item.source, self.clock.time(), sh, sig_bands, url_key, tkey,
                       tickers(item.title))
            self.entries[item.id] = e
            for b in sig_bands:
                self.buckets.setdefault(b, set()).add(item.id)
            if url_key:
                self.by_url.setdefault(url_key, item.id)
            if tkey:
                self.by_title.setdefault(tkey, item.id)
        self.counts[kind] += 1
        return DedupResult(kind=kind, cluster_id=cluster.id, similarity=round(similarity, 3),
                           cluster_size=cluster.size, distinct_sources=len(cluster.sources),
                           first_source=cluster.first_source)

    def stats(self) -> dict:
        return {**self.counts, "window": len(self.entries), "clusters": len(self.clusters)}
