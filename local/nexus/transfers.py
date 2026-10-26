"""¿Se puede mover cada moneda entre MEXC y Binance, por qué red y a qué costo? (solo lectura)

Las diferencias de precio que persisten entre exchanges suelen deberse a depósitos o retiros
suspendidos. El market making cubierto necesita rebalancear inventario con transferencias, así que
para cada moneda candidata se informa, en ambos sentidos, las redes donde el retiro del origen y el
depósito del destino están habilitados y la comisión de retiro (en la moneda y aprox. en USDT).
python -m nexus.transfers BMT,IOST,ATM      (usa las claves de NEXUS_HOME/.env)
"""
import json
import re
import sys

from . import paths

ALIASES = {"BEP20": "BSC", "BSC": "BSC", "BNB SMART CHAIN": "BSC", "ERC20": "ETH", "ETH": "ETH",
           "ETHEREUM": "ETH", "TRC20": "TRX", "TRX": "TRX", "TRON": "TRX", "SOL": "SOL", "SOLANA": "SOL",
           "ARBITRUM": "ARB", "ARB": "ARB", "ARBITRUM ONE": "ARB", "OPTIMISM": "OP", "OP": "OP",
           "POLYGON": "MATIC", "MATIC": "MATIC", "AVAXC": "AVAXC", "AVAX C-CHAIN": "AVAXC",
           "BASE": "BASE", "TON": "TON", "APT": "APT", "SUI": "SUI", "NEAR": "NEAR",
           # nombres de otros exchanges (Gate, KuCoin, Bitget, HTX)
           "ARBITRUMONE": "ARB", "ARBEVM": "ARB", "ARBONE": "ARB", "OPETH": "OP", "BASEEVM": "BASE",
           "AVAX_C": "AVAXC", "AVAXC-CHAIN": "AVAXC", "AVAX-C": "AVAXC", "POLYGON POS": "MATIC",
           "SPL": "SOL", "BEP20(BSC)": "BSC"}


def norm(name):
    """Nombre de red normalizado: 'BEP20(BSC)' -> 'BSC', 'Ethereum(ERC20)' -> 'ETH'."""
    up = (name or "").upper()
    for part in [up] + re.findall(r"[A-Z0-9 \-]+", up):
        part = part.strip()
        if part in ALIASES:
            return ALIASES[part]
    return up


def mexc_networks(cfg):
    out = {}
    for c in cfg:
        nets = {}
        for n in c.get("networkList", []):
            nets[norm(n.get("netWork") or n.get("network"))] = {
                "deposit": bool(n.get("depositEnable")), "withdraw": bool(n.get("withdrawEnable")),
                "fee": float(n.get("withdrawFee") or 0)}
        out[c["coin"]] = nets
    return out


def binance_networks(cfg):
    out = {}
    for c in cfg:
        nets = {}
        for n in c.get("networkList", []):
            nets[norm(n.get("network"))] = {"deposit": bool(n.get("depositEnable")),
                                            "withdraw": bool(n.get("withdrawEnable")),
                                            "fee": float(n.get("withdrawFee") or 0)}
        out[c["coin"]] = nets
    return out


def routes(src, dst):
    """Redes posibles origen->destino (retiro habilitado en origen y depósito en destino), por costo."""
    r = [(net, s["fee"]) for net, s in src.items() if s["withdraw"] and dst.get(net, {}).get("deposit")]
    return sorted(r, key=lambda x: x[1])


def check(coins, mexc_cfg, binance_cfg, prices=None):
    m, b = mexc_networks(mexc_cfg), binance_networks(binance_cfg)
    prices = prices or {}
    out = {}
    for coin in coins:
        mm, bb = m.get(coin, {}), b.get(coin, {})
        r = {}
        for label, src, dst in (("MEXC->Binance", mm, bb), ("Binance->MEXC", bb, mm)):
            rt = routes(src, dst)
            r[label] = ({"net": rt[0][0], "fee": rt[0][1], "fee_usdt": round(rt[0][1] * prices.get(coin, 0), 4)
                         if prices.get(coin) else None, "alternatives": len(rt) - 1} if rt else None)
        r["ok_both_ways"] = bool(r["MEXC->Binance"] and r["Binance->MEXC"])
        out[coin] = r
    return out


def main(coins):
    from .exchanges.binance import BinanceReadOnly
    from .exchanges.mexc import MexcReadOnly
    paths.load_env()
    res = check(coins, MexcReadOnly().capital_config(), BinanceReadOnly().capital_config())
    res["USDT"] = check(["USDT"], MexcReadOnly().capital_config(), BinanceReadOnly().capital_config())["USDT"]
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return res


if __name__ == "__main__":
    main(sys.argv[1].split(","))
