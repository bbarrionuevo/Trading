"""Meta-modelo (meta-labeling): p = P(take-profit antes que stop-loss) para la apuesta que propone la señal.
Se opera solo si p ≥ 0,60 (informe), y p dimensiona con ¼-Kelly.

- Prior, sin datos: logística en |α(Δt)|. Con α = 0 da la probabilidad de que un paseo al azar toque
  +3σ antes que −1,5σ, SL/(SL+TP) = 1/3; con |α| = 0,25 llega a 0,60.
- Aprendizaje que solo reduce (REGLAS C-5): p_κ = 1/3 + κ·(p_prior − 1/3), con κ ∈ [0, 1] ajustado por
  máxima verosimilitud con los resultados reales de la triple barrera (los que mide el etiquetador
  desde la llegada de cada noticia). Por "categoría:lado" si hay n ≥ 30; si no, global con n ≥ 30; si
  no, κ = 1. κ nunca pasa de 1: los datos pueden achicar la confianza del modelo, nunca agrandarla.
"""
import math

BASE = 1 / 3
P_AT = (0.25, 0.60)                           # |α| = 0,25 ⇒ p = 0,60
_A = math.log(BASE / (1 - BASE))
_B = (math.log(P_AT[1] / (1 - P_AT[1])) - _A) / P_AT[0]
MIN_N = 30


def prior(alpha: float) -> float:
    return 1 / (1 + math.exp(-(_A + _B * abs(alpha))))


def shrink(p: float, kappa: float) -> float:
    return BASE + min(1.0, max(0.0, kappa)) * (p - BASE)


def log_lik(pairs: list[tuple[float, int]], kappa: float) -> float:
    total = 0.0
    for p, y in pairs:
        q = min(1 - 1e-9, max(1e-9, shrink(p, kappa)))
        total += math.log(q) if y else math.log(1 - q)
    return total


def fit_kappa(pairs: list[tuple[float, int]], tol: float = 1e-4) -> float:
    """κ de máxima verosimilitud en [0, 1] (la verosimilitud es cóncava en κ: sección áurea)."""
    if not pairs:
        return 1.0
    g = (math.sqrt(5) - 1) / 2
    a, b = 0.0, 1.0
    c, d = b - g * (b - a), a + g * (b - a)
    while b - a > tol:
        if log_lik(pairs, c) >= log_lik(pairs, d):
            b, d = d, c
            c = b - g * (b - a)
        else:
            a, c = c, d
            d = a + g * (b - a)
    k = (a + b) / 2
    return 1.0 if log_lik(pairs, 1.0) >= log_lik(pairs, k) else round(k, 4)


def fit(samples: dict[str, list[tuple[float, int]]], min_n: int = MIN_N) -> dict[str, dict]:
    """{"global" | "categoría:lado": {"kappa", "n", "tasa_tp", "p_media"}} con los grupos que alcanzan n."""
    out = {}
    everything = [x for xs in samples.values() for x in xs]
    for key, xs in [("global", everything), *sorted(samples.items())]:
        if len(xs) >= min_n:
            out[key] = {"kappa": fit_kappa(xs), "n": len(xs), "tasa_tp": round(sum(y for _, y in xs) / len(xs), 4),
                        "p_media": round(sum(p for p, _ in xs) / len(xs), 4)}
    return out


class MetaModel:
    def __init__(self, fitted: dict | None = None):
        self.fitted = fitted or {}

    def kappa(self, category: str, side: str) -> float:
        for key in (f"{category}:{side}", "global"):
            if key in self.fitted:
                return min(1.0, float(self.fitted[key]["kappa"]))
        return 1.0

    def p(self, alpha: float, category: str, side: str) -> float:
        return shrink(prior(alpha), self.kappa(category, side))
