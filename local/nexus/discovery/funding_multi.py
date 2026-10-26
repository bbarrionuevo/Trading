"""Funding de perpetuos en muchos exchanges, normalizado a APR, y diferencias ENTRE exchanges.

Diferencia de funding (delta neutral, sin contado): largo el perpetuo donde el funding es bajo o
negativo y corto donde es alto. El precio se cancela entre las dos patas y queda la diferencia de
funding. Solo hace falta margen en ambos exchanges (sin transferencias mientras dura).
Cada exchange cobra cada 1, 4 u 8 horas: se normaliza a APR con el intervalo real cuando se publica.
Fuentes públicas que responden desde servidores de EE. UU.: Bitget, KuCoin, HTX, OKX, Gate, MEXC,
Hyperliquid (Bybit y Binance Futuros bloquean esa región).
"""
import re

import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
EXTREME_APR = 300.0        # una pata por encima de esto: token nuevo o squeeze, inestable y de alto riesgo
TAKER_PCT = {"Bitget": 0.06, "KuCoin": 0.06, "HTX": 0.05, "OKX": 0.05, "Gate": 0.05, "MEXC": 0.02,
             "Hyperliquid": 0.045}


def _get(url, **kw):
    r = S.get(url, timeout=(5, 25), **kw)
    r.raise_for_status()
    return r.json()


def norm_base(b):
    """Mismo activo con distinto nombre: XBT=BTC, kPEPE (Hyperliquid) = 1000PEPE = PEPE."""
    b = b or ""
    if len(b) > 2 and b[0] == "k" and b[1:].isupper():
        b = b[1:]
    b = b.upper()
    b = {"XBT": "BTC"}.get(b, b)
    return re.sub(r"^(1000000|1000|1M)(?=[A-Z])", "", b)


def apr(rate, interval_h):
    return rate * (24 / interval_h) * 365 * 100


def row(ex, base, rate, interval_h, vol_usd, interval_known=True, symbol=None, px=None):
    return {"exchange": ex, "base": norm_base(base), "rate": rate, "interval_h": interval_h,
            "interval_known": interval_known, "apr": apr(rate, interval_h), "vol_usd": vol_usd, "symbol": symbol,
            "px": px}


def bitget():
    out = []
    for t in _get("https://api.bitget.com/api/v2/mix/market/tickers", params={"productType": "USDT-FUTURES"})["data"]:
        if t["symbol"].endswith("USDT") and t.get("fundingRate") not in (None, ""):
            out.append(row("Bitget", t["symbol"][:-4], float(t["fundingRate"]), 8, float(t.get("usdtVolume") or 0),
                           interval_known=False, symbol=t["symbol"], px=_f(t.get("lastPr"))))
    return out


def _f(x):
    try:
        return float(x) or None
    except (TypeError, ValueError):
        return None


def bitget_interval(symbol):
    d = _get("https://api.bitget.com/api/v2/mix/market/current-fund-rate",
             params={"symbol": symbol, "productType": "USDT-FUTURES"})["data"]
    return float(d[0]["fundingRateInterval"]) if d and d[0].get("fundingRateInterval") else None


def kucoin():
    out = []
    for c in _get("https://api-futures.kucoin.com/api/v1/contracts/active")["data"]:
        if c.get("quoteCurrency") == "USDT" and c.get("fundingFeeRate") is not None and c.get("status") == "Open":
            gran = c.get("fundingRateGranularity") or 28_800_000
            out.append(row("KuCoin", c["baseCurrency"], float(c["fundingFeeRate"]), gran / 3_600_000,
                           float(c.get("turnoverOf24h") or 0), symbol=c["symbol"],
                           px=_f(c.get("markPrice") or c.get("lastTradePrice"))))
    return out


def htx():
    vol = {}
    try:
        for t in _get("https://api.hbdm.com/v2/linear-swap-ex/market/detail/batch_merged",
                      params={"business_type": "swap"}).get("ticks", []):
            vol[t["contract_code"]] = float(t.get("trade_turnover") or 0)
    except Exception:                                                   # noqa: BLE001 (volumen opcional)
        pass
    out = []
    for c in _get("https://api.hbdm.com/linear-swap-api/v1/swap_batch_funding_rate")["data"]:
        if c["contract_code"].endswith("-USDT") and c.get("funding_rate") is not None:
            out.append(row("HTX", c["symbol"], float(c["funding_rate"]), 8, vol.get(c["contract_code"], 0),
                           interval_known=False, symbol=c["contract_code"]))
    return out


def okx():
    vol, px = {}, {}
    for t in _get("https://www.okx.com/api/v5/market/tickers", params={"instType": "SWAP"})["data"]:
        try:
            vol[t["instId"]] = float(t["volCcy24h"]) * float(t["last"])
            px[t["instId"]] = float(t["last"])
        except (KeyError, TypeError, ValueError):
            pass
    out = []
    for f in _get("https://www.okx.com/api/v5/public/funding-rate", params={"instId": "ANY"})["data"]:
        inst = f["instId"]
        if not inst.endswith("-USDT-SWAP") or f.get("fundingRate") in (None, ""):
            continue
        try:
            interval = (int(f["fundingTime"]) - int(f["prevFundingTime"])) / 3_600_000
        except (KeyError, TypeError, ValueError):
            interval = 0
        known = interval > 0
        out.append(row("OKX", inst.split("-")[0], float(f["fundingRate"]), interval if known else 8,
                       vol.get(inst, 0), interval_known=known, symbol=inst, px=px.get(inst)))
    return out


def gate():
    vol = {t["contract"]: float(t.get("volume_24h_quote") or 0)
           for t in _get("https://api.gateio.ws/api/v4/futures/usdt/tickers")}
    out = []
    for c in _get("https://api.gateio.ws/api/v4/futures/usdt/contracts"):
        if c.get("in_delisting") or c.get("funding_rate") in (None, ""):
            continue
        out.append(row("Gate", c["name"].split("_")[0], float(c["funding_rate"]),
                       (c.get("funding_interval") or 28800) / 3600, vol.get(c["name"], 0), symbol=c["name"],
                       px=_f(c.get("mark_price"))))
    return out


def mexc():
    cycle = {}
    try:
        for d in _get("https://contract.mexc.com/api/v1/contract/detail").get("data", []):
            for k in ("fundingRateCycle", "collectCycle", "fundingCycle"):
                if d.get(k):
                    cycle[d["symbol"]] = float(d[k])
                    break
    except Exception:                                                   # noqa: BLE001
        pass
    out = []
    for t in _get("https://contract.mexc.com/api/v1/contract/ticker").get("data", []):
        if t["symbol"].endswith("_USDT") and t.get("fundingRate") is not None:
            known = t["symbol"] in cycle
            out.append(row("MEXC", t["symbol"].split("_")[0], float(t["fundingRate"]), cycle.get(t["symbol"], 8),
                           float(t.get("amount24") or 0), interval_known=known, symbol=t["symbol"],
                           px=_f(t.get("lastPrice"))))
    return out


def hyperliquid():
    meta, ctxs = S.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}, timeout=(5, 25)).json()
    out = []
    for u, c in zip(meta["universe"], ctxs):
        if u.get("isDelisted") or c.get("funding") is None:
            continue
        out.append(row("Hyperliquid", u["name"], float(c["funding"]), 1, float(c.get("dayNtlVlm") or 0),
                       symbol=u["name"], px=_f(c.get("markPx"))))
    return out


def refine_intervals(rows, bases, limit=60):
    """Consulta el intervalo real de cobro (MEXC, Bitget) de las monedas candidatas y recalcula el APR:
    asumir 8 h cuando el par cobra cada 4 h o 1 h subestima el APR 2 a 8 veces."""
    n = 0
    for r in rows:
        if r["interval_known"] or r["base"] not in bases or n >= limit:
            continue
        try:
            if r["exchange"] == "MEXC":
                sym = r.get("symbol") or f"{r['base']}_USDT"
                h = (_get(f"https://contract.mexc.com/api/v1/contract/funding_rate/{sym}").get("data") or {}).get("collectCycle")
            elif r["exchange"] == "Bitget":
                h = bitget_interval(r.get("symbol") or f"{r['base']}USDT")
            else:
                continue
            n += 1
            if h:
                r["interval_h"], r["interval_known"] = float(h), True
                r["apr"] = apr(r["rate"], r["interval_h"])
        except Exception:                                               # noqa: BLE001 (se deja el supuesto)
            continue
    return rows


SOURCES = {"Bitget": bitget, "KuCoin": kucoin, "HTX": htx, "OKX": okx, "Gate": gate, "MEXC": mexc,
           "Hyperliquid": hyperliquid}


def fetch_all():
    rows, errors = [], {}
    for name, fn in SOURCES.items():
        try:
            rows += fn()
        except Exception as e:                                          # noqa: BLE001 (fuente opcional)
            errors[name] = f"{type(e).__name__}: {str(e)[:100]}"
    return rows, errors


def cross_spreads(rows, min_vol=1_000_000, hold_days=7):
    """Por moneda: corto donde el APR es más alto y largo donde es más bajo, con volumen suficiente.
    Neto a `hold_days` días = diferencia acumulada menos 4 ejecuciones tomadoras (entrar y salir)."""
    by = {}
    for r in rows:
        if r["vol_usd"] >= min_vol:
            by.setdefault(r["base"], []).append(r)
    out = []
    for base, rs in by.items():
        if len({r["exchange"] for r in rs}) < 2:
            continue
        hi, lo = max(rs, key=lambda r: r["apr"]), min(rs, key=lambda r: r["apr"])
        if hi["exchange"] == lo["exchange"]:
            continue
        spread = hi["apr"] - lo["apr"]
        cost = 2 * (TAKER_PCT.get(hi["exchange"], 0.06) + TAKER_PCT.get(lo["exchange"], 0.06))
        out.append({"base": base, "extreme": max(abs(hi["apr"]), abs(lo["apr"])) > EXTREME_APR,
                    "short_symbol": hi.get("symbol"), "long_symbol": lo.get("symbol"),
                    "short_px": hi.get("px"), "long_px": lo.get("px"),
                    "short": hi["exchange"], "short_apr": round(hi["apr"], 1),
                    "long": lo["exchange"], "long_apr": round(lo["apr"], 1), "spread_apr": round(spread, 1),
                    "cost_pct": round(cost, 3), "net_hold_pct": round(spread / 365 * hold_days - cost, 3),
                    "breakeven_days": round(cost / (spread / 365), 1) if spread > 0 else None,
                    "intervals_known": hi["interval_known"] and lo["interval_known"],
                    "min_vol_usd": round(min(hi["vol_usd"], lo["vol_usd"]))})
    return sorted(out, key=lambda x: -x["spread_apr"])


def history(ex, symbol, days=7):
    """[(ts_segundos, tasa)] liquidadas en los últimos `days` días para una pata, o [] si no hay dato."""
    import time as _t
    since = _t.time() - days * 86400
    if ex == "OKX":
        d = _get("https://www.okx.com/api/v5/public/funding-rate-history", params={"instId": symbol, "limit": 100})["data"]
        out = [(int(x["fundingTime"]) / 1000, float(x.get("realizedRate") or x["fundingRate"])) for x in d]
    elif ex == "KuCoin":
        d = _get("https://api-futures.kucoin.com/api/v1/contract/funding-rates",
                 params={"symbol": symbol, "from": int(since * 1000), "to": int(_t.time() * 1000)})["data"]
        out = [(int(x["timepoint"]) / 1000, float(x["fundingRate"])) for x in d]
    elif ex == "Gate":
        d = _get("https://api.gateio.ws/api/v4/futures/usdt/funding_rate", params={"contract": symbol, "limit": 200})
        out = [(int(x["t"]), float(x["r"])) for x in d]
    elif ex == "Bitget":
        d = _get("https://api.bitget.com/api/v2/mix/market/history-fund-rate",
                 params={"symbol": symbol, "productType": "USDT-FUTURES", "pageSize": 100})["data"]
        out = [(int(x["fundingTime"]) / 1000, float(x["fundingRate"])) for x in d]
    elif ex == "MEXC":
        d = _get("https://contract.mexc.com/api/v1/contract/funding_rate/history",
                 params={"symbol": symbol, "page_num": 1, "page_size": 100}).get("data") or {}
        out = [(int(x["settleTime"]) / 1000, float(x["fundingRate"])) for x in d.get("resultList") or []]
    elif ex == "Hyperliquid":
        d = S.post("https://api.hyperliquid.xyz/info", json={"type": "fundingHistory", "coin": symbol,
                                                              "startTime": int(since * 1000)}, timeout=(5, 25)).json()
        out = [(int(x["time"]) / 1000, float(x["fundingRate"])) for x in d]
    elif ex == "HTX":
        d = _get("https://api.hbdm.com/linear-swap-api/v1/swap_historical_funding_rate",
                 params={"contract_code": symbol, "page_size": 50}).get("data") or {}
        out = [(int(x["funding_time"]) / 1000, float(x.get("realized_rate") or x["funding_rate"]))
               for x in d.get("data") or []]
    else:
        return []
    return sorted(x for x in out if x[0] >= since)


def realized_spread(short_hist, long_hist, days=7):
    """Lo que habría cobrado la diferencia en la ventana: el corto cobra su tasa y el largo paga la suya.
    Devuelve (total %, APR %, fracción de días con resultado positivo) o None si falta historial."""
    if not short_hist or not long_hist:
        return None
    span = min(short_hist[-1][0] - short_hist[0][0], long_hist[-1][0] - long_hist[0][0]) / 86400
    if span < 1:
        return None
    t0 = max(short_hist[0][0], long_hist[0][0])
    s = [x for x in short_hist if x[0] >= t0]
    lg = [x for x in long_hist if x[0] >= t0]
    total = (sum(r for _, r in s) - sum(r for _, r in lg)) * 100
    days_n = max(1, int(span))
    daily = {}
    for t, r in s:
        daily[int((t - t0) // 86400)] = daily.get(int((t - t0) // 86400), 0) + r
    for t, r in lg:
        daily[int((t - t0) // 86400)] = daily.get(int((t - t0) // 86400), 0) - r
    pos = sum(v > 0 for v in daily.values()) / max(1, len(daily))
    return round(total, 3), round(total / span * 365, 1), round(pos, 2), round(span, 1)


def verify_top(cross, rows, n=8, days=7):
    """Agrega a las mejores diferencias lo REALMENTE cobrado en los últimos días (historial de ambas patas)."""
    sym = {(r["exchange"], r["base"]): r.get("symbol") for r in rows}
    for x in cross[:n]:
        try:
            sh = history(x["short"], sym.get((x["short"], x["base"])), days)
            lg = history(x["long"], sym.get((x["long"], x["base"])), days)
            x["realized"] = realized_spread(sh, lg, days)
        except Exception as e:                                          # noqa: BLE001
            x["realized"] = None
            x["realized_error"] = f"{type(e).__name__}"
    return cross
