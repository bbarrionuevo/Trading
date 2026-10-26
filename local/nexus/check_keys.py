"""Verifica las claves de NEXUS_HOME/.env SIN mostrarlas: MEXC, Binance y Kraken.

python -m nexus.check_keys
Muestra si cada clave responde, permisos que informa la cuenta, cantidad de activos con saldo y,
para MEXC, la comisión real. CriptoYa no usa clave: su API es pública.
Código de salida: 0 todo bien · 1 alguna clave falla · 2 alguna cuenta informa retiros habilitados
(R-01: para operar real, la clave debe crearse SIN permiso de retiro y con lista blanca de IP).
"""
import os
import re
import sys
import time

import requests
import yaml

from . import paths
from .agent.maker_agent import CFG
from .exchanges.binance import BinanceReadOnly
from .exchanges.kraken import KrakenReadOnly
from .exchanges.mexc import MexcReadOnly, parse_fee


SECRET_LIKE = re.compile(r"[A-Za-z0-9+/=_-]{28,}")
TIME_URLS = {"MEXC": ("https://api.mexc.com/api/v3/time", lambda j: j["serverTime"]),
             "Binance": ("https://api.binance.com/api/v3/time", lambda j: j["serverTime"]),
             "Kraken": ("https://api.kraken.com/0/public/Time", lambda j: j["result"]["unixtime"] * 1000)}
HINTS = {"700002": "firma inválida: ¿clave y secreto intercambiados o con espacios?",
         "700003": "hora desfasada: sincronizá Windows (Configuración > Hora > Sincronizar ahora)",
         "700006": "la clave está limitada a otra IP: agregá la IP de esta PC en MEXC",
         "700007": "la clave no tiene permiso de lectura de cuenta: tildá 'ver cuenta' en MEXC",
         "10072": "clave inválida o vencida: creá una nueva en MEXC",
         "-1021": "hora desfasada: sincronizá Windows",
         "-2015": "clave inválida, IP no permitida o sin permiso de lectura (Binance)",
         "EAPI:Invalid key": "clave de Kraken inválida",
         "EAPI:Invalid nonce": "nonce inválido: hora desfasada o clave usada en otro programa",
         "EGeneral:Permission denied": "a la clave de Kraken le falta el permiso 'Query Funds'"}


def describe_error(e):
    """Mensaje útil y SEGURO: código HTTP, código y texto del exchange, y una pista. Nunca la clave."""
    resp = getattr(e, "response", None)
    body = ""
    if resp is not None:
        body = SECRET_LIKE.sub("[oculto]", (resp.text or "")[:300])
    elif str(e).startswith("['") or "EAPI" in str(e) or "EGeneral" in str(e):
        body = str(e)[:300]
    hint = next((h for code, h in HINTS.items() if code in body), "")
    status = f"HTTP {resp.status_code} " if resp is not None else ""
    return f"{type(e).__name__} {status}{body}" + (f" -> {hint}" if hint else "")


def clock_offset_ms(name):
    url, get = TIME_URLS[name]
    t0 = time.time() * 1000
    server = get(requests.get(url, timeout=10).json())
    return server - (t0 + time.time() * 1000) / 2


def _nonzero(balances, key_free="free"):
    return sum(1 for b in balances if float(b.get(key_free, 0) or 0) > 0)


def check_mexc(c):
    acc = c.account()
    fees = {}
    for sym in yaml.safe_load(CFG.read_text(encoding="utf-8"))["symbols"]:
        try:
            fees[sym] = parse_fee(c.trade_fee(sym))
        except Exception as e:
            fees[sym] = f"sin dato ({type(e).__name__})"
    return {"canTrade": acc.get("canTrade"), "canWithdraw": acc.get("canWithdraw"),
            "permissions": acc.get("permissions"), "activos_con_saldo": _nonzero(acc.get("balances", [])),
            "comision_maker_taker_pct": fees}


def key_warnings(r):
    """Problemas de los permisos de una clave de Binance (vacío = bien para la cobertura)."""
    w = []
    if r.get("enableWithdrawals"):
        w.append("LA CLAVE PERMITE RETIROS: desactivalo ya en Binance (R-01). La cobertura no se activa así")
    if not r.get("enableSpotAndMarginTrading"):
        w.append("la clave no tiene trading spot: la cobertura no puede operar")
    if r.get("enableInternalTransfer") or r.get("permitsUniversalTransfer"):
        w.append("la clave permite transferencias: no hacen falta, conviene sacarlas (mínimo permiso)")
    if r.get("enableFutures") or r.get("enableMargin") or r.get("enablePortfolioMarginTrading"):
        w.append("la clave permite futuros o margen: no hacen falta, conviene sacarlos")
    if not r.get("ipRestrict"):
        w.append("la clave no está limitada a la IP de la PC: conviene limitarla")
    return w


def check_binance(c):
    acc = c._get("/api/v3/account", signed=True)
    rates = acc.get("commissionRates") or {}
    out = {"canTrade": acc.get("canTrade"), "canWithdraw": acc.get("canWithdraw"),
           "permissions": acc.get("permissions"), "activos_con_saldo": _nonzero(acc.get("balances", [])),
           "comision_maker_taker_pct": (float(rates["maker"]) * 100, float(rates["taker"]) * 100)
           if rates else None}
    try:
        r = c.api_restrictions()
        out["permisos_de_la_clave"] = {"retiros": r.get("enableWithdrawals"), "trading_spot": r.get("enableSpotAndMarginTrading"),
                                       "transferencias": bool(r.get("enableInternalTransfer") or r.get("permitsUniversalTransfer")),
                                       "ip_limitada": r.get("ipRestrict")}
        out["avisos"] = key_warnings(r) or ["permisos correctos para la cobertura"]
    except Exception as e:                                           # noqa: BLE001
        out["permisos_de_la_clave"] = f"sin dato ({type(e).__name__})"
    return out


def check_kraken(c):
    bal = c.balances()
    return {"responde": True, "activos_con_saldo": sum(1 for v in bal.values() if float(v) > 0)}


EXCHANGES = (
    ("MEXC", "MEXC_API_KEY", "MEXC_SECRET_KEY", MexcReadOnly, check_mexc),
    ("Binance", "BINANCE_API_KEY", "BINANCE_SECRET_KEY", BinanceReadOnly, check_binance),
    ("Kraken", "KRAKEN_API_KEY", "KRAKEN_SECRET_KEY", KrakenReadOnly, check_kraken),
)


def format_problems(value):
    """Errores típicos al pegar una clave (sin devolver el valor): prefijos, espacios, comillas."""
    p = []
    if ":" in value:
        p.append("contiene ':' (¿quedó un prefijo como 'key:'?)")
    if any(c.isspace() for c in value):
        p.append("contiene espacios")
    if any(c in value for c in "\"'<>"):
        p.append("contiene comillas o < >")
    return p


def run(exchanges=EXCHANGES, out=print, check_clock=True):
    code = 0
    for name, k, s, cls, fn in exchanges:
        if not (os.environ.get(k) and os.environ.get(s)):
            out(f"{name}: sin clave en .env (se omite)")
            continue
        bad = {var: format_problems(os.environ[var]) for var in (k, s)}
        bad = {var: pr for var, pr in bad.items() if pr}
        if bad:
            for var, pr in bad.items():
                out(f"{name}: FORMATO INCORRECTO en {var}: {', '.join(pr)}. Debe ser {var}=<solo la clave>")
            code = max(code, 1)
            continue
        try:
            off = clock_offset_ms(name) if check_clock and name in TIME_URLS else 0
            if abs(off) > 1000:
                out(f"{name}: ATENCIÓN reloj de la PC desfasado {off / 1000:+.1f} s respecto del exchange")
        except Exception:
            pass
        try:
            r = fn(cls())
        except Exception as e:
            out(f"{name}: ERROR {describe_error(e)}")
            code = max(code, 1)
            continue
        avisos = r.pop("avisos", None)
        out(f"{name}: OK {r}")
        for a in avisos or []:
            out(f"  {name}: {a}")
        perms = r.get("permisos_de_la_clave")
        if isinstance(perms, dict) and perms.get("retiros"):
            code = max(code, 2)                          # la clave misma permite retirar: R-01 roto
        elif r.get("canWithdraw") and not (isinstance(perms, dict) and perms.get("retiros") is False):
            out(f"  ADVERTENCIA R-01 ({name}): la cuenta informa retiros habilitados. Puede ser un dato de la "
                "cuenta y no de la clave: confirmá en la web que la clave NO tenga permiso de retiro.")
            code = max(code, 2)
    out("CriptoYa: no usa clave (API pública).")
    return code


def main():
    paths.load_env()
    return run()


if __name__ == "__main__":
    sys.exit(main())
