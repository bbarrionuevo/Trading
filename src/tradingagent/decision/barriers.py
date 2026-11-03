"""Triple barrera (López de Prado), adaptada a la volatilidad: take-profit, stop-loss y expiración.

- σ = volatilidad de 1 minuto (desvío de los log-retornos de cierre) de la hora ANTERIOR a la noticia:
  la que todavía no está contaminada por el movimiento del evento.
- Con H = minutos hasta la expiración: SL = 1,5·σ·√H y TP = 3·σ·√H (b = TP/SL = 2, la apuesta del
  ¼-Kelly). El SL nunca queda más cerca que `min_stop` (comisiones + ruido) ni más lejos que `max_stop`;
  si se recorta, el TP se mueve con él y b sigue siendo 2.
- `walk` recorre velas de 1 minuto y dice qué barrera se tocó primero. Mismo criterio en el etiquetador
  (aprendizaje), en el operador por corridas y en las pruebas: si una vela toca las dos, cuenta el stop;
  el take-profit se llena solo si el precio lo atraviesa; un salto más allá del stop se llena desde la
  apertura; en el minuto de la entrada solo cuenta el cierre (es lo único seguro posterior a la entrada).
"""
import math
from dataclasses import dataclass

MIN = 60_000


@dataclass(frozen=True)
class BarrierConfig:
    sl_sigma: float = 1.5
    tp_sigma: float = 3.0
    min_stop: float = 0.006          # 0,6 %
    max_stop: float = 0.05           # 5 %
    min_returns: int = 30            # log-retornos necesarios para medir σ


def sigma_1m(candles: dict, end_ms: int, n: int = 60, min_returns: int = 30) -> float | None:
    """Desvío de los log-retornos de 1 minuto de las `n` velas cerradas antes de `end_ms` (None si faltan)."""
    start = (end_ms // MIN - n) * MIN
    closes = [candles[m][3] for m in range(start, end_ms // MIN * MIN, MIN) if m in candles and candles[m][3] > 0]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    if len(rets) < min_returns:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


DEFAULT = BarrierConfig()


def barriers(sigma: float, horizon_min: float, cfg: BarrierConfig = DEFAULT) -> tuple[float, float]:
    """(SL, TP) como fracción del precio de entrada, con b = TP/SL constante."""
    sl = cfg.sl_sigma * sigma * math.sqrt(max(horizon_min, 1.0))
    clamped = min(max(sl, cfg.min_stop), cfg.max_stop)
    return clamped, clamped * cfg.tp_sigma / cfg.sl_sigma


def prices(entry: float, side: int, sl: float, tp: float) -> tuple[float, float]:
    """(precio del stop, precio del take-profit) para un largo (+1) o un corto (-1)."""
    return entry * (1 - side * sl), entry * (1 + side * tp)


def walk(candles: dict, *, side: int, stop: float, tp: float, start_ms: int, end_ms: int, entry_ms: int | None = None,
         until_ms: int | None = None, slip: float = 0.0, trail: float = 0.0,
         state: dict | None = None) -> tuple[str, float, int] | None:
    """Primera barrera tocada entre las velas [start_ms, end_ms]: ("sl" | "tp" | "exp", precio, minuto) o None.

    `slip` empeora el precio del stop y de la expiración (salen como órdenes que toman liquidez).
    `trail` > 0: el stop sigue al mejor precio a esa distancia (nunca vuelve atrás); `state` lleva y devuelve
    {"stop", "best"} entre llamadas. Dentro de una vela primero cuenta lo malo (conservador)."""
    if trail and state is not None:
        stop = float(state.get("stop", stop))
    best = float(state.get("best", 0) or 0) if state else 0.0
    entry_m = None if entry_ms is None else entry_ms // MIN * MIN
    for m in range(start_ms // MIN * MIN, end_ms // MIN * MIN + MIN, MIN):
        k = candles.get(m)
        if k is None:
            continue
        o, h, lo, c = k[0], k[1], k[2], k[3]
        if until_ms is not None and m >= until_ms:
            return "exp", o * (1 - side * slip), m
        if m == entry_m:
            o = h = lo = c
        adverse = lo if side > 0 else h                       # lo peor de la vela para la posición
        favorable = h if side > 0 else lo
        if (adverse <= stop) if side > 0 else (adverse >= stop):
            fill = min(stop, o) if side > 0 else max(stop, o)  # salto más allá del stop: desde la apertura
            return "sl", fill * (1 - side * slip), m
        if (favorable > tp) if side > 0 else (favorable < tp):
            return "tp", tp, m
        if trail:
            best = max(best, favorable) if side > 0 else (min(best, favorable) if best else favorable)
            moved = best * (1 - trail) if side > 0 else best * (1 + trail)
            stop = max(stop, moved) if side > 0 else min(stop, moved)
            if state is not None:
                state.update(stop=stop, best=best)
    return None
