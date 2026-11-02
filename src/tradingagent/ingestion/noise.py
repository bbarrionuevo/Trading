"""Filtro de ruido determinista: clickbait, predicciones de precio, contenido pago y formatos que no son
noticia (podcasts, resúmenes semanales, guías). Cada regla suma un peso y deja su motivo, así cada
descarte es auditable en el diario. Las fuentes oficiales (nivel 1) solo se filtran por vacías.
"""
import re
from dataclasses import dataclass

from ..core.events import TIER_OFFICIAL, TIER_UNVERIFIED, NewsItem, NoiseResult


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    weight: float
    on_body: bool = False


def _r(name, pattern, weight, on_body=False):
    return Rule(name, re.compile(pattern, re.IGNORECASE), weight, on_body)


DEFAULT_RULES = (
    _r("prediccion_precio", r"\bprice (prediction|forecast|analysis)\b|\bcould (hit|reach|soar to|explode)\b|"
                            r"\b(will|can) \w+ (hit|reach) \$", 1.0),
    _r("listado_compras", r"\btop \d+\b|\bbest (crypto|coins?|tokens?|altcoins?|stocks?) to (buy|watch)\b|"
                          r"\b(coins?|tokens?) to (buy|watch)\b", 1.0),
    _r("clickbait", r"\byou won'?t believe\b|\bhere'?s why\b|\bthis is why\b|\bshocking\b|\bmust[- ]see\b", 0.6),
    _r("hype", r"\b\d{2,4}x\b|\bto the moon\b|\bmillionaire\b|\b(massive|huge) (pump|rally) (incoming|soon)\b|"
               r"\bgem\b", 0.8),
    _r("promocional", r"\bsponsored\b|\bpartner content\b|\bpaid (post|content)\b|\bpresale\b|\bgiveaway\b|"
                      r"\bairdrop (is )?live\b|\breferral\b|\bpromo code\b|\bsign[- ]up bonus\b", 1.2, True),
    _r("formato_no_noticia", r"^(podcast|webinar|video|watch|live|opinion|op-ed|explainer|newsletter)\b|"
                             r"\bweek(ly)? (in review|recap)\b|\bdaily (recap|digest|brief)\b|"
                             r"^(how to|what is|guide)\b", 1.0),
)


@dataclass(frozen=True)
class NoiseConfig:
    threshold: float = 1.0
    min_title_words: int = 4
    max_title_words: int = 40
    unverified_penalty: float = 0.3


class NoiseFilter:
    def __init__(self, config: NoiseConfig | None = None, rules=DEFAULT_RULES):
        self.cfg, self.rules = config or NoiseConfig(), rules

    def score(self, item: NewsItem) -> NoiseResult:
        title = item.title.strip()
        reasons, score = [], 0.0
        words = title.split()
        if not title:
            return NoiseResult(score=99.0, is_noise=True, reasons=("titulo_vacio",))
        if item.tier == TIER_OFFICIAL:                    # comunicados oficiales: nunca son ruido
            return NoiseResult(score=0.0, is_noise=False)
        if len(words) < self.cfg.min_title_words:
            score, reasons = score + 0.6, reasons + ["titulo_corto"]
        if len(words) > self.cfg.max_title_words:
            score, reasons = score + 0.6, reasons + ["titulo_largo"]
        letters = [c for c in title if c.isalpha()]
        if len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            score, reasons = score + 0.6, reasons + ["mayusculas"]
        if title.count("!") >= 2:
            score, reasons = score + 0.5, reasons + ["exclamaciones"]
        if sum(1 for c in title if ord(c) >= 0x1F300) >= 2:
            score, reasons = score + 0.5, reasons + ["emojis"]
        for rule in self.rules:
            text = f"{title} {item.body[:600]}" if rule.on_body else title
            if rule.pattern.search(text):
                score, reasons = score + rule.weight, reasons + [rule.name]
        if item.tier >= TIER_UNVERIFIED and reasons:
            score += self.cfg.unverified_penalty
        score = round(score, 3)
        return NoiseResult(score=score, is_noise=score >= self.cfg.threshold, reasons=tuple(reasons))
