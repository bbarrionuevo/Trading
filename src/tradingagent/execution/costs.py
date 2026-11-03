"""Costos de ejecución: ley de la raíz cuadrada (Bouchaud et al.) y Almgren-Chriss (2000).

- Raíz cuadrada: impacto esperado de una orden de tamaño Q con volumen diario V y volatilidad diaria σ_D:
  I = Y·σ_D·√(Q/V), con Y ≈ 0,5-0,7 (se usa 0,6). Es la estimación que entra en la decisión.
- Almgren-Chriss: cómo repartir una orden grande en N tramos de τ minutos minimizando E[C] + λ·Var[C],
  con impacto temporal lineal (η), permanente (γ) y medio spread (ε):
      E[C] = ½γX² + ε·Σ|n_j| + (η̃/τ)·Σn_j²,  η̃ = η − γτ/2
      Var[C] = σ²·τ·Σx_j²
  y trayectoria óptima x_j = X·sinh(κ(T − t_j))/sinh(κT), con cosh(κτ) = 1 + λσ²τ²/(2η̃).
- Urgencia: con una noticia, esperar cuesta alfa (vida media de 120 s). Se suma la deriva en contra
  mientras se espera, μ·τ·Σ n_j·(j − ½), con μ = ventaja esperada × la velocidad de decaimiento. Con
  los tamaños de este agente (decenas de USDT contra millones de volumen) el óptimo es un solo tramo.
Unidades de Almgren-Chriss: X en unidades del activo, T y τ en minutos, σ en USDT por unidad y por √minuto,
η en USDT por unidad por (unidades/minuto), γ en USDT por unidad², λ en 1/USDT; costos en USDT.
"""
import math

SQRT_Y = 0.6


def sqrt_impact(notional: float, adv_notional: float | None, sigma_daily: float, y: float = SQRT_Y) -> float:
    """Impacto esperado como fracción del precio. Sin volumen conocido se asume lo peor razonable (1 %)."""
    if notional <= 0:
        return 0.0
    if not adv_notional or adv_notional <= 0:
        return 0.01
    return y * sigma_daily * math.sqrt(notional / adv_notional)


def round_trip(fee_taker: float, spread: float, impact: float) -> float:
    """Costo de entrar y salir como fracción del nocional: comisión × 2 + el spread entero + impacto × 2."""
    return 2 * fee_taker + spread + 2 * impact


def almgren_chriss(x: float, horizon: float, n: int, sigma: float, eta: float, *, gamma: float = 0.0,
                   eps: float = 0.0, lam: float = 0.0, drift: float = 0.0) -> dict:
    """Trayectoria óptima en `n` tramos y su costo esperado y varianza. `drift` = cuánto se mueve el precio en
    contra por minuto mientras se espera (USDT por unidad y por minuto)."""
    if n < 1 or horizon <= 0 or x <= 0:
        raise ValueError("Almgren-Chriss necesita n ≥ 1, horizonte > 0 y cantidad > 0")
    tau = horizon / n
    eta_t = eta - gamma * tau / 2
    if eta_t <= 0:
        raise ValueError("impacto temporal η̃ no positivo: el modelo no aplica con esos parámetros")
    if lam <= 0 or sigma <= 0:
        holdings = [x * (1 - j / n) for j in range(n + 1)]           # sin aversión al riesgo: lineal (TWAP)
    else:
        kappa = math.acosh(1 + lam * sigma ** 2 * tau ** 2 / (2 * eta_t)) / tau
        holdings = [x * math.sinh(kappa * (horizon - j * tau)) / math.sinh(kappa * horizon) for j in range(n + 1)]
    trades = [a - b for a, b in zip(holdings, holdings[1:], strict=False)]
    expected = (0.5 * gamma * x ** 2 + eps * sum(abs(t) for t in trades) + eta_t / tau * sum(t ** 2 for t in trades)
                + drift * tau * sum(t * (j + 0.5) for j, t in enumerate(trades)))
    variance = sigma ** 2 * tau * sum(h ** 2 for h in holdings[1:])
    return {"tramos": n, "tenencia": holdings, "ordenes": trades, "costo_esperado": expected, "varianza": variance,
            "objetivo": expected + lam * variance}


def eta_from_sqrt(x: float, price: float, impact: float, tau: float = 1.0) -> float:
    """η que hace que ejecutar todo en un tramo de τ minutos cueste lo que dice la raíz cuadrada."""
    return impact * price * tau / x if x > 0 else 0.0


def best_schedule(x: float, price: float, sigma_1m: float, impact: float, *, lam: float = 1e-3,
                  max_slices: int = 10, tau: float = 1.0, drift: float = 0.0) -> dict:
    """Cuántos tramos de `tau` minutos minimizan E[C] + λ·Var[C] (σ_1m como fracción del precio por minuto;
    `drift` en USDT por unidad y por minuto)."""
    sigma = sigma_1m * price
    eta = max(eta_from_sqrt(x, price, impact, tau), 1e-12)
    best = None
    for n in range(1, max_slices + 1):
        r = almgren_chriss(x, n * tau, n, sigma, eta, lam=lam, drift=drift)
        if best is None or r["objetivo"] < best["objetivo"]:
            best = r
    return best
