"""Evaluación contra el sobreajuste: Sharpe probabilístico (PSR) y deflactado (DSR), Bailey y López de
Prado (2012, 2014).

- PSR(SR*) = Φ( (SR − SR*)·√(T−1) / √(1 − γ₃·SR + (γ₄−1)/4·SR²) ): probabilidad de que el Sharpe
  verdadero supere SR*, corrigiendo por asimetría (γ₃) y curtosis (γ₄, 3 en la normal). Penaliza las
  colas pesadas y la asimetría negativa.
- DSR = PSR(SR₀) con SR₀ = √V·((1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e))): el máximo Sharpe que se
  esperaría solo por azar después de probar N variantes (V = varianza de sus Sharpe, γ = Euler-Mascheroni).
- Criterio (REGLAS F-3): DSR ≥ 0,95 para promover; DSR < 0,80 para descartar la versión.
Los Sharpe son por operación (no anualizados): las operaciones guiadas por noticias no son periódicas.
"""
import math
from statistics import NormalDist

EULER_GAMMA = 0.5772156649015329
PROMOTE, DISCARD = 0.95, 0.80
_N = NormalDist()


def moments(xs: list[float]) -> tuple[float, float, float, float]:
    """(media, desvío muestral, asimetría, curtosis no excedente) de una muestra."""
    n = len(xs)
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    if sd == 0:
        return m, 0.0, 0.0, 3.0
    m2 = sum((x - m) ** 2 for x in xs) / n
    skew = sum((x - m) ** 3 for x in xs) / n / m2 ** 1.5
    kurt = sum((x - m) ** 4 for x in xs) / n / m2 ** 2
    return m, sd, skew, kurt


def sharpe(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m, sd, _, _ = moments(xs)
    return m / sd if sd > 0 else None


def psr(sr: float, n: int, skew: float = 0.0, kurt: float = 3.0, sr_star: float = 0.0) -> float:
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if n < 2 or denom <= 0:
        return 0.0
    return _N.cdf((sr - sr_star) * math.sqrt(n - 1) / math.sqrt(denom))


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """SR₀: el Sharpe máximo esperado por azar entre `n_trials` variantes con varianza `var_sr`."""
    if n_trials < 2 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
                                + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def dsr(returns: list[float], n_trials: int = 1, var_sr: float = 0.0) -> float | None:
    """DSR de una serie de rendimientos por operación (None con menos de 2 operaciones o sin dispersión)."""
    sr = sharpe(returns)
    if sr is None:
        return None
    _, _, skew, kurt = moments(returns)
    return psr(sr, len(returns), skew, kurt, expected_max_sharpe(n_trials, var_sr))


def verdict(value: float | None, n: int, min_n: int = 30) -> str:
    """promover | descartar | en_evaluacion (con menos de `min_n` operaciones no se juzga)."""
    if value is None or n < min_n:
        return "en_evaluacion"
    if value >= PROMOTE:
        return "promover"
    if value < DISCARD:
        return "descartar"
    return "en_evaluacion"


def by_version(trades: list[dict], min_n: int = 30) -> dict[str, dict]:
    """Evidencia por versión de estrategia (`strategy_tag`, REGLAS C-5) con el DSR corregido por la cantidad
    de versiones probadas y la dispersión de sus Sharpe."""
    rets: dict[str, list[float]] = {}
    for t in trades:
        if t.get("tipo") == "salida" and t.get("notional_entrada"):
            rets.setdefault(t.get("strategy_tag", "?"), []).append(float(t["pnl_neto"]) / float(t["notional_entrada"]))
    srs = [s for s in (sharpe(r) for r in rets.values()) if s is not None]
    n_trials = max(1, len(rets))
    var_sr = (sum((s - sum(srs) / len(srs)) ** 2 for s in srs) / (len(srs) - 1)) if len(srs) > 1 else 0.0
    out = {}
    for tag, r in sorted(rets.items()):
        d = dsr(r, n_trials, var_sr)
        out[tag] = {"n": len(r), "sharpe": None if sharpe(r) is None else round(sharpe(r), 4),
                    "dsr": None if d is None else round(d, 4), "veredicto": verdict(d, len(r), min_n)}
    return out
