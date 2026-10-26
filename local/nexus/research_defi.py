"""¿Queda lugar para un bot nuevo en DeFi? Medición on-chain con RPC públicos (sin claves ni fondos).

Liquidaciones de Aave v3 (Base y Arbitrum): cuántas hubo, cuánto valían y QUÉ PARTE se llevan los
principales liquidadores. Si pocos bots se quedan con casi todo, el mercado ya está tomado por
profesionales (latencia y subastas de gas) y un recién llegado no gana nada aunque el bot funcione.
python -m nexus.research_defi [dias]
"""
import json
import sys
import time
from collections import Counter

import requests

LIQ_TOPIC = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"   # LiquidationCall(...)
CHAINS = {
    "Base": {"rpc": "https://mainnet.base.org", "pool": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5", "block_s": 2.0,
             "tokens": {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6),
                        "0x4200000000000000000000000000000000000006": ("WETH", 18)}},
    "Arbitrum": {"rpc": "https://arb1.arbitrum.io/rpc", "pool": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
                 "block_s": 0.25,
                 "tokens": {"0xaf88d065e77cc8cc2239327c5edb3a432268e5831": ("USDC", 6),
                            "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": ("USDC.e", 6),
                            "0xfd086bc7cd5c481dcc9c85ee1478a1c0b69fcbb9": ("USDT", 6),
                            "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": ("WETH", 18)}},
}
S = requests.Session()


def rpc(url, method, params):
    r = S.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(str(d["error"])[:200])
    return d["result"]


def logs(chain, days, max_calls=400):
    c = CHAINS[chain]
    head = int(rpc(c["rpc"], "eth_blockNumber", []), 16)
    start = head - int(days * 86400 / c["block_s"])
    out, step, to, calls = [], 10_000, head, 0
    while to > start and calls < max_calls:
        frm = max(start, to - step + 1)
        try:
            out += rpc(c["rpc"], "eth_getLogs", [{"address": c["pool"], "topics": [LIQ_TOPIC],
                                                  "fromBlock": hex(frm), "toBlock": hex(to)}])
            to = frm - 1
            step = min(step * 2, 200_000)
        except Exception:                                               # noqa: BLE001 (rango grande: achicar)
            step = max(step // 2, 500)
            time.sleep(0.5)
        calls += 1
    covered = (head - max(to, start)) * c["block_s"] / 86400
    return out, round(covered, 2)


def summarize(chain, raw, eth_usd):
    tokens = CHAINS[chain]["tokens"]
    by_liq_n, by_liq_usd, total_usd, known = Counter(), Counter(), 0.0, 0
    for lg in raw:
        debt = "0x" + lg["topics"][2][-40:]
        data = lg["data"][2:]
        debt_to_cover = int(data[0:64], 16)
        liquidator = "0x" + data[128 + 24:192]
        by_liq_n[liquidator] += 1
        if debt.lower() in tokens:
            sym, dec = tokens[debt.lower()]
            usd = debt_to_cover / 10 ** dec * (eth_usd if sym == "WETH" else 1.0)
            by_liq_usd[liquidator] += usd
            total_usd += usd
            known += 1
    n = len(raw)
    top3_n = sum(c for _, c in by_liq_n.most_common(3)) / n if n else 0
    top3_usd = sum(v for _, v in by_liq_usd.most_common(3)) / total_usd if total_usd else 0
    return {"chain": chain, "liquidations": n, "liquidators": len(by_liq_n), "top3_share_count": round(top3_n, 3),
            "top3_share_usd": round(top3_usd, 3), "debt_usd_known": round(total_usd), "known_debt_events": known,
            "bonus_usd_est_5pct": round(total_usd * 0.05),
            "top": [(a[:10], c) for a, c in by_liq_n.most_common(5)]}


def main(days=7):
    try:
        eth = float(S.get("https://data-api.binance.vision/api/v3/ticker/price", params={"symbol": "ETHUSDT"},
                          timeout=15).json()["price"])
    except Exception:                                                   # noqa: BLE001
        eth = 0.0
    res = []
    for chain in CHAINS:
        try:
            raw, covered = logs(chain, float(days))
            s = summarize(chain, raw, eth)
            s["days_covered"] = covered
            res.append(s)
        except Exception as e:                                          # noqa: BLE001
            res.append({"chain": chain, "error": f"{type(e).__name__}: {str(e)[:150]}"})
    print(json.dumps({"eth_usd": eth, "results": res}, indent=1))
    return res


if __name__ == "__main__":
    main(*sys.argv[1:2])
