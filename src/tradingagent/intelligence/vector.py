"""Vector de evento y decaimiento del alfa (informe "Trading Sistemático por Eventos").

Cada noticia, para cada activo, se resume en cinco dimensiones:
- C · correlación entidad-activo (0-10): si el activo es el protagonista o una referencia colateral.
  Nombrado en el titular 10 (9 si hay varios), solo en el cuerpo 7, sugerido por la fuente 6. Sin
  activo, BTC como referencia del mercado: 7 si la categoría mueve a todo el mercado (macro,
  regulación, ETF...), 3 si no;
- P · polaridad (-1..+1): la dirección de la señal escalada por su certeza (rumor, desmentido,
  señales encontradas o "ya se movió" la achican);
- I · importancia intrínseca (0-10): la gravedad del hecho (quiebra 9, anuncio menor 4), +1 si la
  fuente es oficial, −2 si no está verificada, +0,5 por cada fuente extra que lo corrobora (hasta 1,5);
- M · impacto proyectado (0-10): magnitud esperada sobre el precio, a priori por categoría;
- D · duración (0-10): persistencia del efecto, del horizonte de la categoría (30 min = 3 … 4 h = 9).
  La expiración de la triple barrera es ese horizonte.
Si la IA leyó la noticia (llm.py), C, I y M son el menor entre su lectura y estos a priori: la IA corrige lo
que las reglas sobreestiman (un hackeo chico en un exchange que se llama "Mercado Bitcoin" no mueve BTC).

α_E = P·(C/10)·(I/10)·(M/10). La señal pasa solo con C ≥ C_min (7) y |α| ≥ α_min. El alfa decae con
vida media t½ = 120 s desde la publicación: α(Δt) = α_E · 2^(−Δt/t½). Con Δt > 3·t½ = 360 s la
noticia es obsoleta. Sin hora de publicación creíble, Δt = desde la llegada + una vida media (REGLAS
B-5: lo raro nunca se toma a ciegas).
"""
from dataclasses import asdict, dataclass

from ..core.events import TIER_OFFICIAL, TIER_UNVERIFIED
from ..research import assets as asset_mod
from .classifier import MARKET_WIDE, Signal

IMPORTANCE = {"quiebra": 9, "hack": 8, "deslistado": 8, "etf": 8, "listado": 7, "macro": 7, "regulatorio": 7,
              "fusion": 8, "resultados": 7, "caida_red": 6, "desbloqueo": 5, "tesoreria": 5, "adopcion": 4,
              "quema": 4, "mejora_red": 4}
MAGNITUDE = {"quiebra": 9, "hack": 8, "deslistado": 8, "listado": 8, "fusion": 8, "etf": 7, "resultados": 7,
             "macro": 6, "regulatorio": 6, "caida_red": 5, "desbloqueo": 4, "tesoreria": 4, "adopcion": 3,
             "quema": 3, "mejora_red": 3}
DURATION = ((30, 3), (60, 5), (120, 7), (240, 9))           # horizonte (min) → D
HALF_LIFE_S = 120.0
STALE_HALF_LIVES = 3
MAX_PUB_LAG_S = 3 * 3600


@dataclass(frozen=True)
class EventVector:
    C: float
    P: float
    I: float  # noqa: E741 (la letra del informe)
    M: float
    D: float

    @property
    def alpha(self) -> float:
        return self.P * (self.C / 10) * (self.I / 10) * (self.M / 10)

    def as_dict(self) -> dict:
        return {k: round(v, 3) for k, v in asdict(self).items()} | {"alpha": round(self.alpha, 4)}


def relevance(asset: str, sig: Signal, title: str, body: str = "", hint: tuple = ()) -> float:
    """C: cuánto habla la noticia de ESE activo."""
    if asset in asset_mod.detect(title):
        return 10.0 if len(sig.assets) == 1 else 9.0
    if asset in asset_mod.detect(f"{title}. {body[:600]}"):
        return 7.0
    if asset in hint:
        return 6.0
    if asset == asset_mod.MARKET_PROXY:
        return 7.0 if sig.category in MARKET_WIDE else 3.0
    return 0.0


def importance(category: str, tier: int = 2, cluster_sources: int = 1) -> float:
    base = IMPORTANCE.get(category, 0)
    if not base:
        return 0.0
    base += 1 if tier == TIER_OFFICIAL else -2 if tier >= TIER_UNVERIFIED else 0
    base += 0.5 * min(max(cluster_sources - 1, 0), 3)
    return float(min(10.0, max(0.0, base)))


def duration(horizon_min: float) -> float:
    for h, d in DURATION:
        if horizon_min <= h:
            return float(d)
    return 10.0


def _read(prior: float, ai: float | None) -> float:
    """Lo que leyó la IA solo puede achicar el a priori calibrado, nunca agrandarlo (C-3)."""
    return prior if ai is None else min(prior, float(ai))


def vector(sig: Signal, asset: str, event: dict, magnitude: dict | None = None) -> EventVector:
    """Vector de la noticia `event` (dict del recolector o del agente) para `asset`. Si la IA leyó la noticia,
    C, I y M son el menor entre su lectura (relevancia, importancia, impacto) y el a priori de las reglas."""
    c = relevance(asset, sig, event.get("title", ""), event.get("body", event.get("snippet", "")),
                  tuple(event.get("assets_hint", ())))
    i = importance(sig.category, event.get("tier", 2), event.get("cluster_sources", 1))
    m = float((magnitude or MAGNITUDE).get(sig.category, 0))
    return EventVector(C=_read(c, sig.relevance), P=round(sig.polarity * sig.certainty, 4),
                       I=_read(i, sig.importance), M=_read(m, sig.impact), D=duration(sig.horizon_min))


def age_s(event: dict, now: float) -> tuple[float, str]:
    """(Δt para el decaimiento, base): desde la publicación si es creíble; si no, la llegada + una vida media."""
    pub, recv = event.get("published_at"), float(event["received_at"])
    if pub is not None and 0 <= recv - float(pub) <= MAX_PUB_LAG_S:
        return max(0.0, now - float(pub)), "publicada"
    return max(0.0, now - recv) + HALF_LIFE_S, "llegada+vida_media"


def decay(alpha: float, dt_s: float, half_life_s: float = HALF_LIFE_S) -> float:
    return alpha * 2 ** (-dt_s / half_life_s)


def stale(dt_s: float, half_life_s: float = HALF_LIFE_S) -> bool:
    return dt_s > STALE_HALF_LIVES * half_life_s
