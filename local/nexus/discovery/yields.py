"""Rendimientos de bajo riesgo de mercado y eventos de exchanges (sin claves).

- Préstamo de stablecoins: Bitfinex (margin funding de USD y USDT, se puede automatizar por API),
  OKX (préstamo simple) y Binance Simple Earn (flexible, por tramos).
- Launchpools de MEXC: se deposita un activo (a veces USDT) y se cobra en el token nuevo; el APR en %
  suele ser alto con cupo por usuario, ideal para capital chico.
- Anuncios de Bitget con palabras clave de eventos (Launchpool, PoolX, Earn, Candybomb).
- DeFi (DefiLlama): pools de stablecoins sin pérdida impermanente, con TVL grande, ordenados por el
  rendimiento BASE (comisiones/intereses reales; los premios en tokens se informan aparte porque se
  pueden cortar). Riesgo: contratos inteligentes y depeg, no el precio.
"""
import time

import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
EVENT_WORDS = ("launchpool", "poolx", "earn", "candybomb", "airdrop", "apr", "launchpad", "staking")


def _get(url, **kw):
    r = S.get(url, timeout=(5, 20), **kw)
    r.raise_for_status()
    return r.json()


def lending():
    out, errors = [], {}
    try:
        for t in _get("https://api-pub.bitfinex.com/v2/tickers", params={"symbols": "fUSD,fUST"}):
            # [SÍMBOLO, FRR, BID, BID_PERIOD, BID_SIZE, ASK, ...]: tasas DIARIAS
            out.append({"venue": "Bitfinex", "asset": {"fUSD": "USD", "fUST": "USDT"}[t[0]],
                        "apr_pct": round(float(t[1]) * 365 * 100, 2), "note": "tasa FRR diaria x 365; varía cada hora"})
    except Exception as e:                                              # noqa: BLE001
        errors["Bitfinex"] = f"{type(e).__name__}: {str(e)[:80]}"
    try:
        for d in _get("https://www.okx.com/api/v5/finance/savings/lending-rate-summary", params={"ccy": "USDT"})["data"]:
            out.append({"venue": "OKX", "asset": d["ccy"], "apr_pct": round(float(d["estRate"]) * 100, 2),
                        "note": "préstamo simple, tasa estimada"})
    except Exception as e:                                              # noqa: BLE001
        errors["OKX"] = f"{type(e).__name__}: {str(e)[:80]}"
    try:
        d = _get("https://www.binance.com/bapi/earn/v1/friendly/finance-earn/simple/product/simpleEarnProducts",
                 params={"asset": "USDT", "pageIndex": 1, "pageSize": 5})["data"]["list"]
        for p in d:
            out.append({"venue": "Binance", "asset": p["asset"], "apr_pct": round(float(p["highestApy"]) * 100, 2),
                        "note": "Simple Earn flexible; el máximo suele ser solo para el primer tramo"})
    except Exception as e:                                              # noqa: BLE001
        errors["Binance"] = f"{type(e).__name__}: {str(e)[:80]}"
    return sorted(out, key=lambda x: -x["apr_pct"]), errors


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def mexc_launchpools(now=None):
    """Launchpools de MEXC no terminados, con sus pools (moneda a depositar, tipo, APR si lo publica)."""
    now = now or time.time()
    data = _get("https://www.mexc.com/api/operateactivity/launchpool/list").get("data") or []
    out = []
    for a in data:
        if a.get("activityStatus") == "FINISHED" or (a.get("endTime") or 0) / 1000 < now:
            continue
        for p in a.get("launchpoolDetailList") or []:
            apr = None
            for k, v in p.items():
                if "apr" in k.lower() or "apy" in k.lower():
                    apr = _num(v) if _num(v) is not None else apr
            out.append({"activity": a.get("activityName"), "coin": a.get("activityCoin"),
                        "status": a.get("activityStatus"), "ends": time.strftime("%Y-%m-%d %H:%M UTC",
                                                                                 time.gmtime((a.get("endTime") or 0) / 1000)),
                        "stake": p.get("pledgeCurrency"), "pool_type": p.get("poolType"), "join": p.get("joinType"),
                        "apr": apr, "raw_keys": sorted(k for k in p if any(w in k.lower() for w in
                                                                          ("apr", "apy", "limit", "max", "min", "total")))})
    return out


def bitget_events(days=14, now=None):
    now = now or time.time()
    out = []
    for lang in ("en_US",):
        for a in _get("https://api.bitget.com/api/v2/public/annoucements", params={"language": lang}).get("data") or []:
            title = a.get("annTitle") or ""
            if any(w in title.lower() for w in EVENT_WORDS) and now - int(a.get("cTime") or 0) / 1000 < days * 86400:
                out.append({"title": title, "url": a.get("annUrl"),
                            "date": time.strftime("%Y-%m-%d", time.gmtime(int(a.get("cTime") or 0) / 1000))})
    return out


DEFI_CHAINS = {"Ethereum", "Arbitrum", "Base", "Optimism", "Polygon", "Avalanche", "BSC", "Solana"}
# Protocolos de préstamo consolidados (años en producción, auditados, TVL de miles de millones). El resto
# suelen ser "dólares sintéticos" o protocolos nuevos: pagan más porque arriesgan más (depeg, contratos).
BLUECHIP = {"aave-v3", "compound-v3", "morpho-blue", "morpho-v1", "spark", "sparklend", "sky-lending",
            "maker-dsr", "fluid-lending", "euler-v2", "kamino-lend", "venus-core-pool"}


def defi_stable_pools(raw, min_tvl=20e6, top=12):
    """Pools de stablecoins sin pérdida impermanente, ordenados por APY base (sin premios)."""
    out = []
    for p in raw.get("data") or []:
        try:
            if not p.get("stablecoin") or p.get("ilRisk") != "no" or p.get("chain") not in DEFI_CHAINS:
                continue
            if float(p.get("tvlUsd") or 0) < min_tvl or p.get("outlier"):
                continue
            base = float(p.get("apyBase") or 0)
            out.append({"project": p.get("project"), "chain": p.get("chain"), "symbol": p.get("symbol"),
                        "tvl_musd": round(float(p["tvlUsd"]) / 1e6, 1), "apy_base": round(base, 2),
                        "apy_reward": round(float(p.get("apyReward") or 0), 2),
                        "apy_30d_mean": round(float(p.get("apyMean30d") or 0), 2),
                        "bluechip": p.get("project") in BLUECHIP})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: -x["apy_base"])
    return [x for x in out if x["bluechip"]][:top] + [x for x in out if not x["bluechip"]][:top]


def defi_yields():
    return defi_stable_pools(_get("https://yields.llama.fi/pools"))
