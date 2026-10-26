"""Estado PÚBLICO de depósitos y retiros por red en varios exchanges (sin claves).

Sirve para saber si una diferencia de precio entre exchanges se puede aprovechar: si en un lado
los depósitos o retiros de esa moneda están cerrados, la brecha no se cierra porque nadie puede
mover el activo, y una cobertura que dependa de rebalancear no es realizable.
Formato común: {MONEDA: {RED: {"deposit": bool, "withdraw": bool, "fee": float | None}}}.
Cada parser es puro (testeable); los fetch pueden fallar según el país del servidor.
"""
import requests

from ..transfers import norm

S = requests.Session()
SOURCES = {
    "MEXC": ("https://api.mexc.com/open/api/v2/market/coin/list",
             "https://www.mexc.com/open/api/v2/market/coin/list"),
    "Binance": "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll",
    "Gate": "https://api.gateio.ws/api/v4/spot/currencies",
    "KuCoin": "https://api.kucoin.com/api/v3/currencies",
    "Bitget": "https://api.bitget.com/api/v2/spot/public/coins",
    "HTX": "https://api.huobi.pro/v2/reference/currencies",
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _b(x):
    return x is True or str(x).lower() in ("true", "1", "allowed")


def parse_mexc(raw):
    out = {}
    for c in raw.get("data") or []:
        out[c["currency"].upper()] = {norm(n.get("chain")): {
            "deposit": _b(n.get("is_deposit_enabled")), "withdraw": _b(n.get("is_withdraw_enabled")),
            "fee": _f(n.get("fee"))} for n in c.get("coins") or []}
    return out


def parse_binance(raw):
    out = {}
    for c in raw.get("data") or []:
        out[c["coin"].upper()] = {norm(n.get("network")): {
            "deposit": _b(n.get("depositEnable")), "withdraw": _b(n.get("withdrawEnable")),
            "fee": _f(n.get("withdrawFee"))} for n in c.get("networkList") or []}
    return out


def parse_gate(raw):
    out = {}
    for c in raw:
        if c.get("delisted"):
            continue
        cur = c["currency"].upper()
        chains = c.get("chains") or [{"name": c.get("chain"), "deposit_disabled": c.get("deposit_disabled"),
                                      "withdraw_disabled": c.get("withdraw_disabled")}]
        nets = out.setdefault(cur.split("_")[0], {})
        for n in chains:
            nets[norm(n.get("name"))] = {"deposit": not n.get("deposit_disabled"),
                                         "withdraw": not (n.get("withdraw_disabled") or n.get("withdraw_delayed")),
                                         "fee": None}
    return out


def parse_kucoin(raw):
    out = {}
    for c in raw.get("data") or []:
        out[c["currency"].upper()] = {norm(n.get("chainName")): {
            "deposit": _b(n.get("isDepositEnabled")), "withdraw": _b(n.get("isWithdrawEnabled")),
            "fee": _f(n.get("withdrawalMinFee"))} for n in c.get("chains") or []}
    return out


def parse_bitget(raw):
    out = {}
    for c in raw.get("data") or []:
        out[c["coin"].upper()] = {norm(n.get("chain")): {
            "deposit": _b(n.get("rechargeable")), "withdraw": _b(n.get("withdrawable")),
            "fee": _f(n.get("withdrawFee"))} for n in c.get("chains") or []}
    return out


def parse_htx(raw):
    out = {}
    for c in raw.get("data") or []:
        out[c["currency"].upper()] = {norm(n.get("displayName") or n.get("baseChain") or n.get("chain")): {
            "deposit": n.get("depositStatus") == "allowed", "withdraw": n.get("withdrawStatus") == "allowed",
            "fee": _f(n.get("transactFeeWithdraw"))} for n in c.get("chains") or []}
    return out


PARSERS = {"MEXC": parse_mexc, "Binance": parse_binance, "Gate": parse_gate, "KuCoin": parse_kucoin,
           "Bitget": parse_bitget, "HTX": parse_htx}


def fetch_all(names=None):
    """{exchange: datos normalizados} y {exchange: error} para los que no respondieron."""
    data, errors = {}, {}
    for name in names or SOURCES:
        urls = SOURCES[name] if isinstance(SOURCES[name], tuple) else (SOURCES[name],)
        for url in urls:
            try:
                r = S.get(url, timeout=(5, 20), headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                parsed = PARSERS[name](r.json())
                if not parsed:
                    raise ValueError("respuesta vacía")
                data[name] = parsed
                errors.pop(name, None)
                break
            except Exception as e:                                    # noqa: BLE001 (fuente opcional)
                errors[name] = f"{type(e).__name__}: {str(e)[:120]}"
    return data, errors


def status(nets, coin):
    """'abierto' si algún red permite depositar y retirar; si no, qué está cerrado."""
    n = nets.get(coin)
    if n is None:
        return "no listada"
    if any(v["deposit"] and v["withdraw"] for v in n.values()):
        return "abierto"
    dep, wd = any(v["deposit"] for v in n.values()), any(v["withdraw"] for v in n.values())
    return "depósito y retiro cerrados" if not dep and not wd else ("retiro cerrado" if dep else "depósito cerrado")


def best_route(src, dst, coin):
    """(red, comisión) más barata con retiro en src y depósito en dst, o None."""
    a, b = src.get(coin, {}), dst.get(coin, {})
    r = [(net, s["fee"]) for net, s in a.items() if s["withdraw"] and b.get(net, {}).get("deposit")]
    return min(r, key=lambda x: (x[1] is None, x[1] or 0)) if r else None
