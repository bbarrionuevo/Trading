"""Etiquetado de noticias con la reacción real del precio: los datos con los que se entrena la etapa 2.

Velas de 1 minuto PÚBLICAS, sin claves: Binance (data-api.binance.vision, el dominio de solo datos de
mercado, accesible desde servidores en EE. UU.) y, si Binance no responde, Coinbase (par XXX-USD).

Para cada noticia y cada par:
- t0 = hora de PUBLICACIÓN si la fuente la da y es creíble (entre 0 y 3 h antes de recibirla); si no,
  la de llegada. `t0_basis` dice cuál se usó y `lag_s` cuánto tardó en llegar;
- p0 = apertura del minuto de t0; pre_10m = lo que se movió en los 10 minutos previos (si ya se movió,
  la noticia ya estaba en el precio);
- r_1m, r_5m, r_15m, r_60m = retorno hasta la apertura del minuto t0 + k;
- mfe_60m / mae_60m = máximo a favor y en contra dentro de la hora (para stops y take-profits);
- r_recv = cuánto ya se había movido al recibirla; r_recv_15m / r_recv_60m = lo que quedaba por
  capturar desde la llegada (lo que un agente con esa latencia podía ganar);
- triple barrera desde la llegada (decision/barriers.py): entrada a la apertura del minuto siguiente a la
  llegada, σ de 1 minuto de la hora previa a t0, SL 1,5σ√60 y TP 3σ√60, expiración a los 60 minutos.
  `tb_largo` y `tb_corto` = 1 (tocó el TP), −1 (el SL) o 0 (expiró), con su retorno bruto (`*_r`). Es la
  etiqueta con la que aprende el meta-modelo.
Se etiqueta cuando pasaron 62 minutos desde la llegada. Las velas se piden por par y por rango: un
pedido cubre todas las noticias cercanas (poco tráfico contra la fuente de datos). Cada vela guarda
también el volumen en USDT y, si la fuente lo da, el comprador agresivo (impacto y lambda de Kyle).
"""
import asyncio
import logging

import aiohttp

from ..core.redact import redact
from ..decision.barriers import barriers, sigma_1m, walk

log = logging.getLogger("tradingagent.labeler")
BINANCE_URL = "https://data-api.binance.vision/api/v3/klines"
COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
MIN = 60_000
HORIZONS = (1, 5, 15, 60)
READY_AFTER_S = 62 * 60
MAX_PUB_LAG_S = 3 * 3600
MERGE_GAP_MIN = 60                  # dos rangos del mismo par a menos de 1 h se piden juntos
SIGMA_MIN = 60                      # minutos antes de t0 para medir σ
TB_HORIZON_MIN = 60

# apertura (ms) → (open, high, low, close, volumen USDT, compra agresiva USDT o None)
Candles = dict[int, tuple]


class LabelError(Exception):
    pass


async def _get_json(session, url: str, params: dict, timeout_s: float, what: str):
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
            if r.status != 200:
                raise LabelError(f"{what}: HTTP {r.status}")
            return await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise LabelError(f"{what}: {type(e).__name__}: {redact(e)}") from e


async def binance(session, symbol: str, start_ms: int, end_ms: int, *, url: str = BINANCE_URL,
                  timeout_s: float = 10.0) -> Candles:
    out: Candles = {}
    t = start_ms
    while t <= end_ms:
        chunk_end = min(end_ms, t + 999 * MIN)
        data = await _get_json(session, url, {"symbol": symbol, "interval": "1m", "startTime": str(t),
                                              "endTime": str(chunk_end), "limit": "1000"}, timeout_s,
                               f"binance {symbol}")
        try:
            for k in data:
                out[int(k[0])] = (float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                                  float(k[7]) if len(k) > 7 else None, float(k[10]) if len(k) > 10 else None)
        except (TypeError, ValueError, IndexError, KeyError) as e:
            raise LabelError(f"binance {symbol}: velas con formato inesperado") from e
        t = chunk_end + MIN
    return out


async def coinbase(session, symbol: str, start_ms: int, end_ms: int, *, url: str = COINBASE_URL,
                   timeout_s: float = 10.0) -> Candles:
    """Coinbase Exchange: [tiempo s, low, high, open, close, volumen], hasta 300 velas por pedido."""
    product = symbol.removesuffix("USDT") + "-USD"
    out: Candles = {}
    t = start_ms
    while t <= end_ms:
        chunk_end = min(end_ms, t + 299 * MIN)
        data = await _get_json(session, url.format(product=product),
                               {"granularity": "60", "start": str(t // 1000), "end": str(chunk_end // 1000)},
                               timeout_s, f"coinbase {product}")
        try:
            for k in data:
                out[int(k[0]) * 1000] = (float(k[3]), float(k[2]), float(k[1]), float(k[4]),
                                         float(k[5]) * float(k[4]) if len(k) > 5 else None, None)
        except (TypeError, ValueError, IndexError, KeyError) as e:
            raise LabelError(f"coinbase {product}: velas con formato inesperado") from e
        t = chunk_end + MIN
    return out


PROVIDERS = (("binance", binance, BINANCE_URL), ("coinbase", coinbase, COINBASE_URL))


def minute(t_s: float) -> int:
    return int(t_s * 1000) // MIN * MIN


def basis(event: dict) -> tuple[float, str]:
    """(t0, base): la publicación si es creíble; si no, la llegada."""
    recv, pub = float(event["received_at"]), event.get("published_at")
    if pub is not None and 0 <= recv - float(pub) <= MAX_PUB_LAG_S:
        return float(pub), "publicada"
    return recv, "recibida"


def window(event: dict) -> tuple[int, int]:
    """Velas necesarias (ms, inclusive): de la hora antes de t0 (σ) a 61 min después de la llegada."""
    t0, _ = basis(event)
    return minute(t0) - (SIGMA_MIN + 10) * MIN, minute(float(event["received_at"])) + (TB_HORIZON_MIN + 1) * MIN


def merge(ranges: list[tuple[int, int]], gap_min: int = MERGE_GAP_MIN) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + gap_min * MIN:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def reaction(candles: Candles, t0_s: float, t_recv_s: float | None = None) -> dict | None:
    """Reacción a partir de las velas; None si faltan velas clave (el par no operó o hay un hueco)."""
    m0 = minute(t0_s)
    first = candles.get(m0)
    if first is None or first[0] <= 0:
        return None
    p0 = first[0]
    before = candles.get(m0 - 10 * MIN)
    out = {"p0": p0, "pre_10m": round(p0 / before[0] - 1, 6) if before and before[0] > 0 else None}
    for k in HORIZONS:
        c = candles.get(m0 + k * MIN)
        out[f"r_{k}m"] = round(c[0] / p0 - 1, 6) if c else None
    hour = [candles[m0 + i * MIN] for i in range(60) if m0 + i * MIN in candles]
    if len(hour) < 50:                                     # hueco grande: la hora no es comparable
        return None
    out["mfe_60m"] = round(max(c[1] for c in hour) / p0 - 1, 6)
    out["mae_60m"] = round(min(c[2] for c in hour) / p0 - 1, 6)
    mr = minute(t0_s if t_recv_s is None else t_recv_s)
    cr = candles.get(mr)
    out["r_recv"] = round(cr[0] / p0 - 1, 6) if cr else None
    for k in (15, 60):
        c = candles.get(mr + k * MIN)
        out[f"r_recv_{k}m"] = round(c[0] / cr[0] - 1, 6) if c and cr else None
    out.update(triple_barrier(candles, m0, mr))
    return out


def triple_barrier(candles: Candles, m0: int, m_recv: int, horizon_min: int = TB_HORIZON_MIN) -> dict:
    """Etiquetas de triple barrera para un largo y un corto que entran a la apertura del minuto siguiente a la
    llegada (sin mirar el futuro), con σ de la hora previa a la noticia."""
    sig = sigma_1m(candles, m0, SIGMA_MIN)
    entry_m = m_recv + MIN
    first = candles.get(entry_m)
    if sig is None or first is None or first[0] <= 0:
        return {"sigma_1m": None if sig is None else round(sig, 7)}
    sl, tp = barriers(sig, horizon_min)
    out = {"sigma_1m": round(sig, 7), "tb_sl": round(sl, 6), "tb_tp": round(tp, 6)}
    entry, until = first[0], entry_m + horizon_min * MIN
    for side, name in ((1, "largo"), (-1, "corto")):
        stop, take = entry * (1 - side * sl), entry * (1 + side * tp)
        hit = walk(candles, side=side, stop=stop, tp=take, start_ms=entry_m, end_ms=until, until_ms=until)
        if hit is None:                                        # faltan velas al final: sin etiqueta
            continue
        kind, price, _ = hit
        out[f"tb_{name}"] = {"tp": 1, "sl": -1, "exp": 0}[kind]
        out[f"tb_{name}_r"] = round(side * (price / entry - 1), 6)
    return out


class CandleStore:
    """Velas de una corrida: cada par se pide una vez por rango combinado, con respaldo de proveedor."""

    def __init__(self, session, providers=PROVIDERS, urls: dict | None = None):
        self.session, self.providers, self.urls = session, providers, urls or {}
        self.candles: dict[str, Candles] = {}
        self.source: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        self.requests = 0

    async def _load_symbol(self, symbol: str, ranges: list[tuple[int, int]]) -> None:
        errors = []
        for name, fetch, default_url in self.providers:
            got: Candles = {}
            try:
                for a, b in merge(ranges):
                    self.requests += 1
                    got.update(await fetch(self.session, symbol, a, b, url=self.urls.get(name, default_url)))
            except Exception as e:                                # noqa: BLE001 (un proveedor no rompe la corrida)
                errors.append(redact(e)[:160] if isinstance(e, LabelError) else f"{name} {symbol}: {type(e).__name__}")
                continue
            self.candles[symbol], self.source[symbol] = got, name
            return
        self.errors[symbol] = " · ".join(errors) or "sin proveedores"

    async def load(self, needs: dict[str, list[tuple[int, int]]]) -> None:
        await asyncio.gather(*(self._load_symbol(s, r) for s, r in needs.items() if s not in self.candles))

    def label(self, event: dict, symbols: list[str]) -> list[dict]:
        """Filas de etiqueta de un evento: una por par; los pares sin datos quedan con `error`."""
        t0, why = basis(event)
        recv = float(event["received_at"])
        rows = []
        for sym in symbols:
            base = {"event_id": event["id"], "symbol": sym, "t0": t0, "t0_basis": why, "t_recv": recv,
                    "lag_s": round(recv - t0, 1)}
            if sym in self.errors:
                rows.append({**base, "error": self.errors[sym], "retry": True})
                continue
            r = reaction(self.candles.get(sym, {}), t0, recv)
            rows.append({**base, "src": self.source.get(sym), **r} if r else {**base, "error": "velas incompletas"})
        return rows
